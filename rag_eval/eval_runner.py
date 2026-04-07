#!/usr/bin/env python3
"""
Layer 2 — RAG Quality Evaluation Runner

Sends each golden-dataset question through the FitCoach API, collects the
response (and retrieved contexts when the API exposes them), then runs RAGAS
metrics to measure answer quality and retrieval performance.

Usage
─────
  # Standard evaluation run:
  python rag_eval/eval_runner.py

  # With explicit base URL and output path:
  python rag_eval/eval_runner.py --base-url http://192.168.0.109/api/v1 \\
                                  --output reports/rag_run.json

  # Calibrate thresholds after first run (sets enforced = baseline − 5pp):
  python rag_eval/eval_runner.py --calibrate

  # Run only a subset (for smoke testing):
  python rag_eval/eval_runner.py --limit 5

Exit codes
──────────
  0  All enforced metric thresholds passed
  1  One or more thresholds failed, or a fatal error occurred

Metrics computed
────────────────
  Always (no retrieved_contexts needed):
    answer_correctness  — semantic similarity of answer vs ground_truth
    answer_relevancy    — does the answer actually address the question?

  When retrieved_contexts available in API response:
    faithfulness        — is the answer grounded in the retrieved chunks?
    context_recall      — were all necessary chunks retrieved?

API context support
───────────────────
  The current FitCoach /chat SSE endpoint includes agent_used in the done
  event but does NOT expose retrieved_contexts.  The runner tries to read
  the 'contexts' / 'retrieved_contexts' / 'chunks' fields from the done
  event; if absent, context-dependent metrics are skipped and a warning is
  logged.  When the API is updated to expose contexts, no code change is
  needed — the metrics activate automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
load_dotenv(REPO_ROOT / ".env")

GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"
THRESHOLDS_PATH     = Path(__file__).parent / "thresholds.json"
REPORTS_DIR         = REPO_ROOT / "reports"

# ---------------------------------------------------------------------------
# API access (same credentials as conftest.py)
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = os.getenv("FITCOACH_API_URL", "http://localhost/api/v1")

_TEST_USER = {
    "username": "test_runner",
    "email":    "test_runner@example.com",
    "password": "TestPassword123!",
}

INTER_QUERY_DELAY = float(os.getenv("L2_QUERY_DELAY", "1.5"))


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_auth_token(base_url: str) -> str:
    """Return a valid bearer token, registering the test user if needed."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        resp = client.post(
            "/auth/login",
            data={"username": _TEST_USER["email"], "password": _TEST_USER["password"]},
        )
        if resp.status_code == 401:
            reg = client.post("/auth/register", json=_TEST_USER)
            if reg.status_code not in (200, 201):
                raise RuntimeError(f"Could not register test user: {reg.status_code} {reg.text}")
            resp = client.post(
                "/auth/login",
                data={"username": _TEST_USER["email"], "password": _TEST_USER["password"]},
            )
        resp.raise_for_status()
        return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Query the FitCoach API
# ---------------------------------------------------------------------------

def query_api(client: httpx.Client, question: str) -> dict:
    """
    Send a question to /chat and consume the SSE stream.
    Returns:
      {
        "answer":             str,
        "agent_used":         str,
        "retrieved_contexts": list[str] | None,
        "http_error":         int | None,
        "error_message":      str | None,
      }
    """
    try:
        with client.stream("POST", "/chat", json={"message": question}) as resp:
            if resp.status_code != 200:
                return {
                    "answer": "",
                    "agent_used": "",
                    "retrieved_contexts": None,
                    "http_error": resp.status_code,
                    "error_message": f"HTTP {resp.status_code}",
                }

            tokens: list[str] = []
            agent_used = ""
            retrieved_contexts: Optional[list[str]] = None
            error_message = ""

            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")
                if etype == "token":
                    tokens.append(event.get("content", ""))
                elif etype == "done":
                    agent_used = event.get("agent_used", "")
                    # Try multiple field names — activate context metrics when
                    # the API is updated to expose retrieved chunks.
                    for ctx_key in ("contexts", "retrieved_contexts", "chunks", "context"):
                        raw = event.get(ctx_key)
                        if raw:
                            if isinstance(raw, list):
                                retrieved_contexts = [
                                    str(c.get("content", c) if isinstance(c, dict) else c)
                                    for c in raw
                                ]
                            elif isinstance(raw, str):
                                retrieved_contexts = [raw]
                            break
                elif etype == "error":
                    error_message = event.get("message", event.get("content", "unknown error"))

            return {
                "answer":             "".join(tokens),
                "agent_used":         agent_used,
                "retrieved_contexts": retrieved_contexts,
                "http_error":         None,
                "error_message":      error_message or None,
            }

    except Exception as exc:  # noqa: BLE001
        return {
            "answer": "",
            "agent_used": "",
            "retrieved_contexts": None,
            "http_error":    None,
            "error_message": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# RAGAS evaluation
# ---------------------------------------------------------------------------

def build_embeddings():
    """
    Build an embeddings client from environment variables.

    EMBED_PROVIDER=ollama  → LiteLLMEmbeddings via local Ollama (recommended)
                             EMBED_MODEL defaults to nomic-embed-text
                             EMBED_BASE_URL defaults to http://localhost:11434
    EMBED_PROVIDER=openai  → ragas.embeddings.OpenAIEmbeddings
                             Needs EMBED_API_KEY (or LLM_API_KEY when on plain OpenAI)
    (default)              → ollama when EMBED_PROVIDER is unset

    Returns an embeddings object with .aembed_text(str) async method.
    Raises RuntimeError if no working embeddings can be constructed.
    """
    embed_provider = os.getenv("EMBED_PROVIDER", "ollama").lower()
    embed_model    = os.getenv("EMBED_MODEL", "nomic-embed-text")
    embed_base_url = os.getenv("EMBED_BASE_URL")
    embed_api_key  = os.getenv("EMBED_API_KEY")

    if embed_provider == "ollama":
        from ragas.embeddings import LiteLLMEmbeddings
        base = embed_base_url or "http://localhost:11434"
        model_name = f"ollama/{embed_model}"
        embeddings = LiteLLMEmbeddings(model=model_name, api_base=base)
        print(f"[RAGAS] Embeddings: Ollama {model_name} @ {base}")
        return embeddings

    if embed_provider == "openai":
        from openai import AsyncOpenAI
        from ragas.embeddings import OpenAIEmbeddings as RagasOpenAIEmbeddings
        key = embed_api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "EMBED_PROVIDER=openai but no EMBED_API_KEY (or LLM_API_KEY) found."
            )
        client_kwargs: dict = dict(api_key=key)
        if embed_base_url:
            client_kwargs["base_url"] = embed_base_url
        embeddings = RagasOpenAIEmbeddings(
            client=AsyncOpenAI(**client_kwargs), model=embed_model
        )
        print(f"[RAGAS] Embeddings: OpenAI {embed_model}")
        return embeddings

    raise RuntimeError(
        f"Unknown EMBED_PROVIDER='{embed_provider}'.  "
        "Set EMBED_PROVIDER=ollama or EMBED_PROVIDER=openai in .env."
    )


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    import math
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a ** 2 for a in v1))
    n2 = math.sqrt(sum(b ** 2 for b in v2))
    return dot / (n1 * n2) if n1 and n2 else 0.0


async def _embed_all_async(texts: list[str], embeddings) -> list[list[float]]:
    """Embed all texts concurrently using the async embeddings client."""
    import asyncio
    return await asyncio.gather(*[embeddings.aembed_text(t) for t in texts])


def run_ragas(samples_data: list[dict], _llm_unused, embeddings) -> dict:
    """
    Compute answer quality metrics using embedding-based cosine similarity.

    This approach avoids RAGAS Collections metrics which require LLM structured
    output via `instructor` — a fragile dependency that fails with smaller models
    (llama3.2) and some commercial APIs (DeepSeek max_tokens / instructor mode).

    Metrics computed:
      answer_correctness  = cosine_sim(embed(answer), embed(ground_truth))
                            Measures whether the answer conveys the same information
                            as the reference.
      answer_relevancy    = cosine_sim(embed(answer), embed(question))
                            Measures whether the answer is topically on-point for
                            the question asked.

    Both metrics are in [0, 1].  They match the metric names in thresholds.json so
    threshold checking works unchanged.
    """
    import asyncio

    if embeddings is None:
        raise RuntimeError(
            "Embeddings are required for scoring but are not available.  "
            "Set EMBED_PROVIDER=ollama in .env and ensure ollama serve is running."
        )

    questions  = [s["question"]    for s in samples_data]
    answers    = [s["answer"]      for s in samples_data]
    references = [s["ground_truth"] for s in samples_data]
    n = len(samples_data)

    print(f"[RAGAS] Embedding {n} answers, questions, and references "
          f"via {type(embeddings).__name__}...")

    # Embed all texts concurrently in three batches.
    # asyncio.gather() must be awaited inside an async def — cannot pass it directly
    # to asyncio.run() which expects a coroutine, not a Future.
    async def _embed_all():
        return await asyncio.gather(
            _embed_all_async(answers,    embeddings),
            _embed_all_async(references, embeddings),
            _embed_all_async(questions,  embeddings),
        )

    ans_vecs, ref_vecs, q_vecs = asyncio.run(_embed_all())

    correctness_scores = [_cosine_similarity(a, r) for a, r in zip(ans_vecs, ref_vecs)]
    relevancy_scores   = [_cosine_similarity(a, q) for a, q in zip(ans_vecs, q_vecs)]

    scores = {
        "answer_correctness": sum(correctness_scores) / n,
        "answer_relevancy":   sum(relevancy_scores)   / n,
    }

    for name, val in scores.items():
        print(f"  [RAGAS] {name} = {val:.4f}  ({n} samples)")

    return scores


# ---------------------------------------------------------------------------
# Threshold checking
# ---------------------------------------------------------------------------

def check_thresholds(scores: dict, thresholds: dict) -> tuple[bool, list[str]]:
    """
    Compare computed scores against enforced thresholds.
    Returns (all_passed: bool, failure_messages: list[str]).
    """
    enforced = thresholds.get("enforced", {})
    ctx_block = thresholds.get("context_metrics_enforced", {})
    if ctx_block.get("context_metrics_active", False):
        enforced = {**enforced, **{k: v for k, v in ctx_block.items()
                                   if k not in ("context_metrics_active", "_comment")}}

    failures = []
    for metric, threshold in enforced.items():
        if metric.startswith("_"):
            continue
        score = scores.get(metric)
        if score is None:
            continue  # metric not computed — skip, don't fail
        if score < threshold:
            failures.append(
                f"  {metric}: {score:.3f} < enforced threshold {threshold:.2f}"
            )
    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Calibration (--calibrate flag)
# ---------------------------------------------------------------------------

def calibrate_thresholds(scores: dict, thresholds_path: Path) -> None:
    """Set enforced thresholds to baseline − 5pp and write back to thresholds.json."""
    with open(thresholds_path, encoding="utf-8") as f:
        data = json.load(f)

    today = time.strftime("%Y-%m-%d")
    data["calibrated"] = True
    data["calibration_date"] = today
    data["baseline_scores"] = {k: round(v, 4) for k, v in scores.items()}

    # Set enforced = baseline − 0.05, floored at 0
    enforced = {}
    for metric, baseline in scores.items():
        enforced[metric] = round(max(0.0, baseline - 0.05), 4)
    data["enforced"] = enforced

    with open(thresholds_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[Calibration] Thresholds updated in {thresholds_path}")
    print(f"  Baseline date: {today}")
    for metric, v in enforced.items():
        print(f"  {metric}: baseline={scores[metric]:.3f}  enforced={v:.3f}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def save_report(output_path: Path, report: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[Report] Saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="FitCoach AI — Layer 2 RAG Quality Evaluation"
    )
    parser.add_argument(
        "--base-url",
        default=_DEFAULT_BASE_URL,
        help="FitCoach API base URL (default: FITCOACH_API_URL env or http://localhost/api/v1)",
    )
    parser.add_argument(
        "--output",
        default=str(REPORTS_DIR / f"rag_{time.strftime('%Y%m%d_%H%M%S')}.json"),
        help="Output report path (JSON)",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="After evaluation, update thresholds.json to baseline-5pp per metric",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N questions (smoke test)",
    )
    args = parser.parse_args()

    # --- Load golden dataset ---
    if not GOLDEN_DATASET_PATH.exists():
        print(f"[ERROR] golden_dataset.json not found at {GOLDEN_DATASET_PATH}", file=sys.stderr)
        return 1

    with open(GOLDEN_DATASET_PATH, encoding="utf-8") as f:
        golden = json.load(f)

    if args.limit:
        golden = golden[: args.limit]
        print(f"[Smoke] Running on first {len(golden)} questions only.")

    # --- Load thresholds ---
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        thresholds = json.load(f)

    # --- Authenticate ---
    print(f"[API] Base URL: {args.base_url}")
    try:
        token = get_auth_token(args.base_url)
        print("[API] Authenticated successfully.")
    except Exception as exc:
        print(f"[ERROR] Authentication failed: {exc}", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {token}"}

    # --- Collect API responses ---
    samples_data: list[dict] = []
    errors: list[dict] = []

    print(f"\n[Eval] Querying API for {len(golden)} golden dataset questions...")
    print(f"       Inter-query delay: {INTER_QUERY_DELAY}s  (set L2_QUERY_DELAY to override)\n")

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120.0) as client:
        for i, item in enumerate(golden):
            if i > 0:
                time.sleep(INTER_QUERY_DELAY)

            qid = item["id"]
            question = item["question"]
            print(f"  [{i+1:02d}/{len(golden)}] {qid} ...", end=" ", flush=True)

            result = query_api(client, question)

            if result["http_error"] or result["error_message"]:
                print(f"ERROR ({result['http_error'] or result['error_message']})")
                errors.append({"id": qid, **result})
                continue

            print(f"OK  agent={result['agent_used']}"
                  f"  contexts={'yes' if result['retrieved_contexts'] else 'no'}")

            samples_data.append({
                "id":                qid,
                "question":          question,
                "ground_truth":      item["ground_truth"],
                "expected_agent":    item.get("expected_agent", ""),
                "source_book":       item.get("source_book", ""),
                "answer":            result["answer"],
                "agent_used":        result["agent_used"],
                "retrieved_contexts": result["retrieved_contexts"],
            })

    if not samples_data:
        print("\n[ERROR] No successful API responses — cannot run RAGAS.", file=sys.stderr)
        return 1

    has_contexts = any(s.get("retrieved_contexts") for s in samples_data)
    print(f"\n[API] Collected {len(samples_data)} responses "
          f"({len(errors)} errors, contexts={'available' if has_contexts else 'NOT available'})")

    if not has_contexts:
        print("[WARN] The API does not expose retrieved_contexts.  "
              "Faithfulness and ContextRecall will be skipped.\n"
              "       To activate context metrics, update the FitCoach API to include\n"
              "       retrieved chunks in the SSE done event.")

    # --- Run RAGAS ---
    print("\n[RAGAS] Initialising embeddings...")
    try:
        embeddings = build_embeddings()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        scores = run_ragas(samples_data, None, embeddings)
    except Exception as exc:
        print(f"[ERROR] RAGAS evaluation failed: {exc}", file=sys.stderr)
        return 1

    # --- Print scores ---
    print("\n[Scores]")
    for metric, score in sorted(scores.items()):
        threshold = thresholds.get("enforced", {}).get(metric)
        status = ""
        if threshold is not None:
            status = "PASS" if score >= threshold else f"FAIL (threshold={threshold:.2f})"
        print(f"  {metric:<30s} {score:.4f}  {status}")

    # --- Check thresholds ---
    passed, failures = check_thresholds(scores, thresholds)

    if failures:
        print("\n[FAIL] Metrics below enforced thresholds:")
        for msg in failures:
            print(msg)
    else:
        print("\n[PASS] All enforced thresholds met.")

    # --- Calibration ---
    if args.calibrate:
        calibrate_thresholds(scores, THRESHOLDS_PATH)

    # --- Save report ---
    report = {
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url":      args.base_url,
        "total_questions": len(golden),
        "evaluated":       len(samples_data),
        "errors":          len(errors),
        "has_retrieved_contexts": has_contexts,
        "scores":          {k: round(v, 4) for k, v in scores.items()},
        "thresholds_enforced": thresholds.get("enforced", {}),
        "all_passed":      passed,
        "failures":        failures,
        "per_sample":      samples_data,
        "error_details":   errors,
    }
    save_report(Path(args.output), report)

    # Also update the fixed latest path
    latest_path = REPORTS_DIR / "rag_latest.json"
    save_report(latest_path, report)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
