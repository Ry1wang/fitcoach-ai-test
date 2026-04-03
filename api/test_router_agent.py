"""
Layer 4 — Router Agent API Behavior Tests
Verifies that the router selects the correct specialist agent for clearly
domain-specific queries. These complement the statistical accuracy tests in
Layer 3 (router_accuracy/) with API-level assertions.
"""

import pytest

from conftest import consume_sse

pytestmark = [pytest.mark.layer4, pytest.mark.requires_corpus]

# Queries with unambiguous domain intent — one correct agent each.
# Labels are drawn from the Layer 1 pass-case analysis (see docs/Layer1_test_res_v1.txt).
CLEAR_ROUTING_CASES = [
    ("What is the correct bar path during a low-bar back squat?", "training"),
    ("How many sets and reps should I do for strength versus hypertrophy?", "training"),
    ("What is a typical recovery timeline after a torn ACL?", "rehab"),
    ("Describe the McGill Big 3 exercises for lower back pain.", "rehab"),
    ("Is creatine monohydrate safe to take daily?", "nutrition"),
    ("What is the recommended protein intake per kilogram of bodyweight for strength athletes?", "nutrition"),
]


class TestRouterClearsingleDomainQueries:
    @pytest.mark.parametrize(
        "query,expected_agent",
        CLEAR_ROUTING_CASES,
        ids=[c[0][:40] for c in CLEAR_ROUTING_CASES],
    )
    def test_clear_domain_query_routes_correctly(
        self, authed_client, query, expected_agent
    ):
        """A clearly domain-specific query must be routed to the correct agent."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200, (
                f"Query returned {resp.status_code}: {resp.text}"
            )
            result = consume_sse(resp)

        assert result["agent_used"] == expected_agent, (
            f"Routing mismatch for query: '{query[:60]}'\n"
            f"  Expected: {expected_agent}\n"
            f"  Actual:   {result['agent_used']}"
        )

    def test_router_always_produces_agent_used(self, authed_client):
        """Every successful response must include a non-empty agent_used."""
        with authed_client.stream(
            "POST", "/chat", json={"message": "How do I squat properly?"}
        ) as resp:
            result = consume_sse(resp)

        assert result["agent_used"], (
            "Response missing agent_used — router did not set a specialist"
        )

    def test_router_response_is_non_empty(self, authed_client):
        """The answer from a routed query must not be empty."""
        with authed_client.stream(
            "POST", "/chat", json={"message": "How do I squat properly?"}
        ) as resp:
            result = consume_sse(resp)

        assert result["answer"], "Routed query returned an empty answer"
