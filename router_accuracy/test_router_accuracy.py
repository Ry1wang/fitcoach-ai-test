"""
Layer 3 — Router Accuracy Tests

Treats the Router Agent as a multi-class classifier and measures routing
accuracy against a human-labeled ground-truth dataset of 105 queries.

Targets (from TestPlan §5 Layer 3):
  - Overall routing accuracy : ≥ 90%
  - Per-class recall          : ≥ 85% per agent

Run with corpus available:
    pytest router_accuracy/ -v -m "layer3"

Run quick smoke (first 10 queries only):
    pytest router_accuracy/ -v -m "layer3" -k "smoke"
"""

import json
import time
import warnings
from collections import defaultdict
from pathlib import Path

import pytest

from conftest import consume_sse

pytestmark = [pytest.mark.layer3, pytest.mark.requires_corpus, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LABELED_DATA_PATH = Path(__file__).parent / "labeled_queries.json"
REPORTS_DIR = Path(__file__).parent.parent / "reports"
OVERALL_ACCURACY_THRESHOLD = 0.90
PER_CLASS_RECALL_THRESHOLD = 0.85
VALID_AGENTS = {"training", "rehab", "nutrition"}

# Rehab-bias alert threshold: warn if rehab handles more than this share of
# all queries (expected theoretical share is ~33%).
REHAB_BIAS_ALERT_THRESHOLD = 0.50


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def labeled_queries() -> list[dict]:
    """Load the labeled query dataset."""
    if not LABELED_DATA_PATH.exists():
        pytest.skip(f"Labeled query file not found: {LABELED_DATA_PATH}")
    with open(LABELED_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def routing_results(authed_client, labeled_queries) -> list[dict]:
    """
    Run every labeled query against the live API and collect routing results.
    Scoped to the module so the full set is executed only once regardless of
    how many test functions consume this fixture.

    Saves raw results to reports/l3_routing_raw_latest.json so that actual
    accuracy numbers and the confusion matrix are persisted beyond the terminal
    session.
    """
    # Delay between queries to avoid exhausting the backend's LLM rate limit.
    # See KNOWN_ISSUES.md — ISSUE-002.
    INTER_QUERY_DELAY = float(
        __import__("os").getenv("L3_QUERY_DELAY", "1.0")
    )

    results = []
    for i, item in enumerate(labeled_queries):
        query_text = item["query"]
        if i > 0:
            time.sleep(INTER_QUERY_DELAY)

        try:
            with authed_client.stream(
                "POST", "/chat", json={"message": query_text}
            ) as resp:
                if resp.status_code != 200:
                    results.append(
                        {
                            **item,
                            "actual_agent": None,
                            "answer": "",
                            "http_error": resp.status_code,
                        }
                    )
                    continue
                sse = consume_sse(resp)
                results.append(
                    {
                        **item,
                        "actual_agent": sse["agent_used"],
                        "answer": sse["answer"],
                        "http_error": None,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    **item,
                    "actual_agent": None,
                    "answer": "",
                    "http_error": str(exc),
                }
            )

    # --- Compute summary metrics and persist to disk ---
    agents = ["training", "rehab", "nutrition"]
    total = len(results)
    single_domain = [r for r in results if r.get("category") != "cross_domain"]
    cross_domain  = [r for r in results if r.get("category") == "cross_domain"]

    # Single-domain accuracy
    sd_correct = sum(1 for r in single_domain if r["actual_agent"] == r["expected_agent"])
    sd_accuracy = sd_correct / len(single_domain) if single_domain else 0.0

    # Per-class recall (single-domain only)
    per_class: dict[str, dict] = {}
    for agent in agents:
        cls = [r for r in single_domain if r["expected_agent"] == agent]
        correct = sum(1 for r in cls if r["actual_agent"] == agent)
        per_class[agent] = {
            "total": len(cls),
            "correct": correct,
            "recall": round(correct / len(cls), 4) if cls else None,
        }

    # Confusion matrix
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in single_domain:
        if r["expected_agent"] and r["actual_agent"]:
            matrix[r["expected_agent"]][r["actual_agent"]] += 1

    # Cross-domain: accepted if actual_agent is in valid_agents list
    cd_accepted = sum(
        1 for r in cross_domain
        if r["actual_agent"] in r.get("valid_agents", [r["expected_agent"]])
    )

    # Rehab share across ALL results
    rehab_actual = sum(1 for r in results if r["actual_agent"] == "rehab")

    summary = {
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_queries": total,
        "single_domain": {
            "count": len(single_domain),
            "correct": sd_correct,
            "accuracy": round(sd_accuracy, 4),
            "threshold": OVERALL_ACCURACY_THRESHOLD,
            "passed": sd_accuracy >= OVERALL_ACCURACY_THRESHOLD,
        },
        "per_class_recall": per_class,
        "cross_domain": {
            "count": len(cross_domain),
            "accepted": cd_accepted,
            "acceptance_rate": round(cd_accepted / len(cross_domain), 4) if cross_domain else None,
        },
        "rehab_share_all": round(rehab_actual / total, 4) if total else 0,
        "confusion_matrix": {exp: dict(row) for exp, row in matrix.items()},
        "per_result": results,
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / "l3_routing_raw_latest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return results


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _confusion_matrix_text(results: list[dict]) -> str:
    """Return a formatted confusion matrix string for diagnostic output."""
    agents = ["training", "rehab", "nutrition"]
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        if r["expected_agent"] and r["actual_agent"]:
            matrix[r["expected_agent"]][r["actual_agent"]] += 1

    col_w = 12
    lines = ["\nRouter Confusion Matrix  (rows = expected, cols = actual)"]
    header = f"{'':>{col_w}}" + "".join(f"{a:>{col_w}}" for a in agents)
    lines.append(header)
    lines.append("-" * (col_w * (len(agents) + 1)))
    for expected in agents:
        row = f"{expected:>{col_w}}" + "".join(
            f"{matrix[expected][actual]:>{col_w}}" for actual in agents
        )
        lines.append(row)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test (subset, fast)
# ---------------------------------------------------------------------------


class TestRouterSmoke:
    """Quick sanity checks — run without corpus flag in CI for fast feedback."""

    pytestmark = [pytest.mark.layer3, pytest.mark.requires_corpus]

    @pytest.mark.parametrize(
        "query,expected_agent",
        [
            ("How many sets and reps should I do for strength?", "training"),
            ("My lower back hurts after deadlifts. What could be causing this?", "rehab"),
            ("Is creatine monohydrate safe to take daily?", "nutrition"),
            ("What is the correct bar position for a low-bar back squat?", "training"),
            ("What is the recommended daily protein intake for a strength athlete?", "nutrition"),
        ],
        ids=["training-sets-reps", "rehab-back-pain", "nutrition-creatine",
             "training-squat-bar", "nutrition-protein"],
    )
    def test_smoke_clear_domain_routing(self, authed_client, query, expected_agent):
        """Five unambiguous queries — one per domain plus extras — must route correctly."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200, (
                f"HTTP {resp.status_code} for query: {query!r}"
            )
            result = consume_sse(resp)

        assert result["agent_used"] in VALID_AGENTS, (
            f"agent_used '{result['agent_used']}' is not a valid agent name"
        )
        assert result["agent_used"] == expected_agent, (
            f"Routing mismatch\n"
            f"  Query:    {query!r}\n"
            f"  Expected: {expected_agent}\n"
            f"  Actual:   {result['agent_used']}"
        )


# ---------------------------------------------------------------------------
# Full accuracy suite (requires live corpus)
# ---------------------------------------------------------------------------


class TestRouterOverallAccuracy:
    """Assert overall routing accuracy meets the 90% target.

    Accuracy is measured on single-domain queries only.  Cross-domain queries
    are evaluated separately in TestCrossDomainBehavior.
    """

    def test_overall_accuracy_meets_threshold(self, routing_results):
        single_domain = [r for r in routing_results if r.get("category") != "cross_domain"]
        total = len(single_domain)
        assert total > 0, "No single-domain results — labeled_queries.json may be empty"

        correct = sum(
            1 for r in single_domain if r["actual_agent"] == r["expected_agent"]
        )
        accuracy = correct / total

        # Print confusion matrix for diagnostics regardless of pass/fail.
        print(_confusion_matrix_text(single_domain))
        print(f"\n[Overall] {correct}/{total} correct = {accuracy:.1%}")

        assert accuracy >= OVERALL_ACCURACY_THRESHOLD, (
            f"Overall router accuracy {accuracy:.1%} is below target "
            f"{OVERALL_ACCURACY_THRESHOLD:.0%}\n"
            f"({correct}/{total} correct)\n"
            + _confusion_matrix_text(single_domain)
        )

    def test_no_null_agent_responses(self, routing_results):
        """Every successful HTTP response must include a non-empty agent_used."""
        null_cases = [
            r for r in routing_results if r["http_error"] is None and not r["actual_agent"]
        ]
        assert not null_cases, (
            f"{len(null_cases)} queries returned HTTP 200 but no agent_used:\n"
            + "\n".join(
                f"  [{r['id']}] {r['query'][:70]}" for r in null_cases
            )
        )

    def test_no_http_errors(self, routing_results):
        """No query should produce a non-200 HTTP response."""
        error_cases = [r for r in routing_results if r["http_error"] is not None]
        assert not error_cases, (
            f"{len(error_cases)} queries produced HTTP errors:\n"
            + "\n".join(
                f"  [{r['id']}] {r['http_error']} — {r['query'][:60]}"
                for r in error_cases
            )
        )


class TestPerClassRecall:
    """Assert per-agent recall meets the 85% threshold (single-domain queries only)."""

    @pytest.mark.parametrize("agent", sorted(VALID_AGENTS))
    def test_per_class_recall(self, routing_results, agent):
        single_domain = [r for r in routing_results if r.get("category") != "cross_domain"]
        class_items = [r for r in single_domain if r["expected_agent"] == agent]
        if not class_items:
            pytest.skip(f"No labeled queries for agent '{agent}'")

        correct = sum(1 for r in class_items if r["actual_agent"] == agent)
        recall = correct / len(class_items)

        misrouted = [
            r for r in class_items if r["actual_agent"] != agent
        ]
        misrouted_summary = "\n".join(
            f"  [{r['id']}] routed→{r['actual_agent']} | {r['query'][:60]}"
            for r in misrouted[:10]  # cap at 10 lines to keep output readable
        )

        assert recall >= PER_CLASS_RECALL_THRESHOLD, (
            f"{agent} recall {recall:.1%} is below target "
            f"{PER_CLASS_RECALL_THRESHOLD:.0%} "
            f"({correct}/{len(class_items)} correct)\n"
            f"Misrouted cases (first 10):\n{misrouted_summary}"
        )


class TestConfusionMatrix:
    """Diagnostic tests — always pass, surface routing patterns for analysis."""

    def test_print_confusion_matrix(self, routing_results):
        """Print full confusion matrix to stdout for manual review."""
        print(_confusion_matrix_text(routing_results))
        # Always passes — informational only.

    def test_rehab_bias_within_acceptable_range(self, routing_results):
        """
        Known issue (P1 from Layer 1 results): rehab agent is over-selected.
        This test quantifies the bias. It hard-fails only when rehab handles
        more than REHAB_BIAS_ALERT_THRESHOLD of all queries, to force
        acknowledgement of severe routing skew. Below that threshold, it emits
        a warning for tracking.
        """
        total = len(routing_results)
        if total == 0:
            pytest.skip("No results to analyse")

        rehab_actual = sum(
            1 for r in routing_results if r["actual_agent"] == "rehab"
        )
        rehab_share = rehab_actual / total

        if rehab_share > REHAB_BIAS_ALERT_THRESHOLD:
            pytest.fail(
                f"Rehab agent bias exceeds alert threshold: "
                f"{rehab_share:.1%} of all queries routed to rehab "
                f"(alert at >{REHAB_BIAS_ALERT_THRESHOLD:.0%}, expected ~33%)\n"
                f"See KNOWN_ISSUES.md — P1 Rehab routing bias."
            )
        elif rehab_share > 0.40:
            # Tightened from 50% to 40% — Layer 1 evidence showed severe bias;
            # warn early so the trend can be tracked even before hard-fail.
            warnings.warn(
                f"Rehab routing share is elevated: {rehab_share:.1%} "
                f"(expected ~33%, soft alert at >40%). See KNOWN_ISSUES.md — P1.",
                UserWarning,
                stacklevel=2,
            )


class TestCrossDomainBehavior:
    """
    Tests for cross-domain queries (category='cross_domain').

    The router only dispatches to a single agent, so these queries cannot be
    'correct' in the multi-agent sense.  Instead we measure:
      1. No 5xx errors — the router must not crash on ambiguous input.
      2. Valid agent returned — the result must be one of the three known agents.
      3. Acceptance rate — actual_agent must be in the query's valid_agents list
         (e.g. for a training+rehab question, either answer is acceptable).
         Target: ≥ 60% (lower than clean-set — intentional, per TestPlan §5 L1).
    """

    def test_cross_domain_no_5xx(self, routing_results):
        """Cross-domain queries must not cause HTTP 5xx errors."""
        cross = [r for r in routing_results if r.get("category") == "cross_domain"]
        if not cross:
            pytest.skip("No cross-domain queries in labeled_queries.json")

        server_errors = [
            r for r in cross
            if isinstance(r.get("http_error"), int) and r["http_error"] >= 500
        ]
        assert not server_errors, (
            f"{len(server_errors)} cross-domain queries caused 5xx errors:\n"
            + "\n".join(
                f"  [{r['id']}] HTTP {r['http_error']}: {r['query'][:60]}"
                for r in server_errors
            )
        )

    def test_cross_domain_returns_valid_agent(self, routing_results):
        """Cross-domain queries must still return a recognised agent name."""
        cross_all = [r for r in routing_results if r.get("category") == "cross_domain"]
        if not cross_all:
            pytest.skip("No cross-domain queries in labeled_queries.json")

        cross = [r for r in cross_all if r["http_error"] is None]
        if not cross:
            error_counts = {}
            for r in cross_all:
                e = r.get("http_error")
                error_counts[e] = error_counts.get(e, 0) + 1
            pytest.skip(
                f"All {len(cross_all)} cross-domain queries returned HTTP errors "
                f"(no successful responses to evaluate): {error_counts}. "
                f"If errors are 429, the rate limit is still active — wait and re-run."
            )

        invalid = [r for r in cross if r["actual_agent"] not in VALID_AGENTS]
        assert not invalid, (
            f"{len(invalid)} cross-domain queries returned an invalid agent:\n"
            + "\n".join(
                f"  [{r['id']}] '{r['actual_agent']}': {r['query'][:60]}"
                for r in invalid
            )
        )

    def test_cross_domain_acceptance_rate(self, routing_results):
        """
        For cross-domain queries, actual_agent must be in the query's
        valid_agents list at least 60% of the time.
        Acceptance rate < 60% suggests the router is ignoring primary intent
        and defaulting to a biased agent (likely rehab).
        """
        CROSS_DOMAIN_ACCEPTANCE_THRESHOLD = 0.60
        cross_all = [r for r in routing_results if r.get("category") == "cross_domain"]
        if not cross_all:
            pytest.skip("No cross-domain queries in labeled_queries.json")

        cross = [r for r in cross_all if r["http_error"] is None]
        if not cross:
            error_counts = {}
            for r in cross_all:
                e = r.get("http_error")
                error_counts[e] = error_counts.get(e, 0) + 1
            pytest.skip(
                f"All {len(cross_all)} cross-domain queries returned HTTP errors "
                f"(no successful responses to evaluate): {error_counts}. "
                f"If errors are 429, the rate limit is still active — wait and re-run."
            )

        accepted = [
            r for r in cross
            if r["actual_agent"] in r.get("valid_agents", [r["expected_agent"]])
        ]
        rate = len(accepted) / len(cross)
        print(f"\n[Cross-domain] acceptance {len(accepted)}/{len(cross)} = {rate:.1%}")

        rejected = [r for r in cross if r not in accepted]
        rejected_summary = "\n".join(
            f"  [{r['id']}] routed→{r['actual_agent']} "
            f"(valid: {r.get('valid_agents')}): {r['query'][:60]}"
            for r in rejected[:10]
        )

        assert rate >= CROSS_DOMAIN_ACCEPTANCE_THRESHOLD, (
            f"Cross-domain acceptance rate {rate:.1%} below target "
            f"{CROSS_DOMAIN_ACCEPTANCE_THRESHOLD:.0%}\n"
            f"Rejected cases (first 10):\n{rejected_summary}"
        )


class TestDifficultyBreakdown:
    """
    Break down accuracy by difficulty label (easy / medium / hard).
    These tests do not enforce thresholds — they surface where routing
    degrades as query complexity increases.
    """

    @pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
    def test_accuracy_by_difficulty(self, routing_results, difficulty):
        subset = [r for r in routing_results if r.get("difficulty") == difficulty]
        if not subset:
            pytest.skip(f"No queries with difficulty='{difficulty}'")

        correct = sum(1 for r in subset if r["actual_agent"] == r["expected_agent"])
        accuracy = correct / len(subset)

        # Print for diagnostics; no hard threshold — just observational.
        print(
            f"\n[difficulty={difficulty}] accuracy={accuracy:.1%} "
            f"({correct}/{len(subset)})"
        )
        # Soft floor: accuracy on easy queries should never fall below 70%.
        if difficulty == "easy":
            assert accuracy >= 0.70, (
                f"Easy-query accuracy {accuracy:.1%} is unexpectedly low "
                f"(floor 70%) — even simple queries are being misrouted."
            )
