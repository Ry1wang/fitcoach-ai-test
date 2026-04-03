#!/usr/bin/env python3
"""
Layer 1: Adversarial Query Generator (Batching + Category Control)
Python Version: 3.11+

Reads corpus metadata from CORPUS_DESIGN.md, prompts LLM in batches,
and enforces strict category distribution defined in CATEGORY_MINIMUMS.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError, model_validator

load_dotenv()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

Category = Literal[
    "cross_domain",
    "ambiguous",
    "emotional",
    "out_of_corpus",
    "author_reference",
    "mixed_language",
    "long_multipart",
]

AgentName = Literal["training", "rehab", "nutrition"]
Answerability = Literal["answerable", "unanswerable"]

CATEGORIES: list[str] = list(Category.__args__)  # type: ignore[attr-defined]


class AdversarialQuery(BaseModel):
    id: str
    query: str
    category: Category
    answerability: Answerability
    expected_agents: list[AgentName]
    created_at: str
    notes: str

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def check_agents_match_answerability(self) -> "AdversarialQuery":
        if self.answerability == "unanswerable" and self.expected_agents:
            raise ValueError("unanswerable queries must have empty agents list")
        if self.answerability == "answerable" and not self.expected_agents:
            raise ValueError("answerable queries must name at least one agent")
        return self


# ---------------------------------------------------------------------------
# Distribution Config
# ---------------------------------------------------------------------------

CATEGORY_MINIMUMS: dict[str, float] = {
    "cross_domain":     0.20,
    "ambiguous":        0.15,
    "emotional":        0.10,
    "out_of_corpus":    0.15,
    "author_reference": 0.10,
    "mixed_language":   0.15,
    "long_multipart":   0.15,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CORPUS_DESIGN_PATH = Path(__file__).parent.parent / "CORPUS_DESIGN.md"


def load_set_b_metadata() -> str:
    if not CORPUS_DESIGN_PATH.exists():
        return "(CORPUS_DESIGN.md not found)"
    content = CORPUS_DESIGN_PATH.read_text(encoding="utf-8")
    match = re.search(r"(### Set B.*?)(?=### Set C|^---)", content, re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else "(Set B section not found)"


def build_category_plan(count: int) -> list[str]:
    """Allocate exactly `count` slots across categories proportionally.

    Uses floor allocation + largest-remainder method to avoid rounding drift.
    Guarantees at least 1 slot per category and an exact total of `count`.
    """
    allocations: dict[str, int] = {}
    for cat, pct in CATEGORY_MINIMUMS.items():
        allocations[cat] = max(1, int(count * pct))

    # Distribute remaining slots to categories with largest fractional remainders
    remaining = count - sum(allocations.values())
    if remaining > 0:
        by_remainder = sorted(
            CATEGORY_MINIMUMS.keys(),
            key=lambda c: (count * CATEGORY_MINIMUMS[c]) - int(count * CATEGORY_MINIMUMS[c]),
            reverse=True,
        )
        for cat in by_remainder[:remaining]:
            allocations[cat] += 1

    plan: list[str] = []
    for cat, n in allocations.items():
        plan.extend([cat] * n)
    return plan


def build_prompt(
    corpus_context: str,
    batch_count: int,
    start_index: int,
    batch_categories: list[str],
) -> str:
    cat_counts = {cat: batch_categories.count(cat) for cat in set(batch_categories)}
    dist_req = "\n".join(
        f"- `{cat}`: {count} query(ies)" for cat, count in cat_counts.items()
    )

    return f"""You are a professional adversarial test designer for FitCoach AI.
The system uses three agents: `training`, `rehab`, and `nutrition`.
System answers ONLY from these books:
---
{corpus_context}
---
TASK:
Generate exactly {batch_count} UNIQUE adversarial test queries as a JSON array.
Start indexing from ID `adv-{start_index:03d}`.

MANDATORY DISTRIBUTION FOR THIS BATCH:
{dist_req}

CATEGORY DEFINITIONS:
- `cross_domain`: Spans 2+ agents. Example: "I have a knee injury but want to bench press — safe?"
- `ambiguous`: Vague intent. Example: "How do I start?"
- `emotional`: High-stress framing. Example: "I'm in constant pain and desperate, HELP."
- `out_of_corpus`: Topic NOT in the list above. MUST be `unanswerable`.
- `author_reference`: Refers to a specific author. Mark `answerable` ONLY if they are in the list above.
- `mixed_language`: Mixed Chinese and English. Example: "我想增肌 but have knee pain — should I squat?"
- `long_multipart`: 100+ words (or 200+ Chinese characters) with multiple sub-questions.

OUTPUT FORMAT:
Return a raw JSON array ONLY. No preamble. Escape all double quotes within strings.
Schema per item:
{{
  "id": "adv-NNN",
  "query": "...",
  "category": "<category_name>",
  "answerability": "answerable" | "unanswerable",
  "expected_agents": ["training", "rehab", "nutrition"],
  "notes": "Failure mode targeted"
}}
"""


def call_llm(prompt: str) -> str | None:
    """Call the configured LLM. Returns the response text, or None on failure."""
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    model_name = os.getenv("LLM_MODEL")

    missing = [k for k, v in {"LLM_API_KEY": api_key, "LLM_BASE_URL": base_url, "LLM_MODEL": model_name}.items() if not v]
    if missing:
        print(f"Error: Missing required .env variable(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Assert to narrow types from str | None → str for the type checker.
    assert api_key and base_url and model_name

    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=4096,
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
    except Exception as e:
        print(f"    ✗ API call failed: {e}", file=sys.stderr)
        return None


def parse_and_validate(raw: str, timestamp: str) -> tuple[list[dict], list[str]]:
    """Parse and validate LLM JSON output.

    Returns (valid_queries, rejection_reasons).
    """
    json_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not json_match:
        return [], ["Response contained no JSON array"]

    try:
        items = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        return [], [f"JSON parse failure: {exc}"]

    valid: list[dict] = []
    errors: list[str] = []

    for item in items:
        item_id = item.get("id", "unknown")
        item["created_at"] = timestamp
        try:
            valid.append(AdversarialQuery.model_validate(item).model_dump())
        except ValidationError as exc:
            first = exc.errors()[0]
            errors.append(f"{item_id}: {first['loc']} — {first['msg']}")

    return valid, errors


# ---------------------------------------------------------------------------
# Main Logic
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batching & Distribution Controlled Case Generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--count", type=int, default=30, help="Total number of queries to generate")
    parser.add_argument("--batch-size", type=int, default=10, help="Queries per LLM call")
    parser.add_argument("--output", type=str, default="adversarial_queries.json", help="Output file path")
    args = parser.parse_args()

    if args.count < len(CATEGORIES):
        parser.error(f"--count must be at least {len(CATEGORIES)} (one per category minimum)")

    plan = build_category_plan(args.count)
    corpus_context = load_set_b_metadata()
    timestamp = datetime.now(timezone.utc).isoformat()
    all_queries: list[dict] = []
    total_rejected = 0

    print(f"Generating {args.count} queries in batches of {args.batch_size}...")

    for i in range(0, args.count, args.batch_size):
        batch_categories = plan[i : i + args.batch_size]
        batch_num = i // args.batch_size + 1
        start_idx = i + 1  # planned position, unaffected by prior validation failures

        print(f"  → Batch {batch_num}: {len(batch_categories)} queries (adv-{start_idx:03d}…)")

        raw_output = call_llm(build_prompt(corpus_context, len(batch_categories), start_idx, batch_categories))

        if raw_output is None:
            print(f"    ✗ Batch {batch_num} skipped due to API failure.", file=sys.stderr)
            continue

        batch_valid, batch_errors = parse_and_validate(raw_output, timestamp)
        all_queries.extend(batch_valid)
        total_rejected += len(batch_errors)

        if batch_errors:
            print(f"    ✗ {len(batch_errors)} item(s) rejected:", file=sys.stderr)
            for reason in batch_errors:
                print(f"      - {reason}", file=sys.stderr)

        print(f"    ✓ {len(batch_valid)} valid  |  {len(batch_errors)} rejected")

        if i + args.batch_size < args.count:
            time.sleep(1)

    if not all_queries:
        print("\nNo valid queries produced. Aborting without writing output.", file=sys.stderr)
        sys.exit(1)

    Path(args.output).write_text(
        json.dumps(all_queries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nDone. {len(all_queries)} saved, {total_rejected} rejected → {args.output}")


if __name__ == "__main__":
    main()
