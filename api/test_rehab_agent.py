"""
Layer 4 — Rehabilitation Agent Endpoint Tests
Verifies that rehab-domain queries are answered by the rehab specialist agent.
"""

import pytest

from conftest import consume_sse

pytestmark = [pytest.mark.layer4, pytest.mark.requires_corpus]

REHAB_QUERIES = [
    "What are the McGill Big 3 exercises for lower back pain?",
    "How long does recovery from a torn ACL typically take?",
    "What exercises are contraindicated after a rotator cuff injury?",
]


class TestRehabAgent:
    @pytest.mark.parametrize("query", REHAB_QUERIES)
    def test_rehab_query_selects_rehab_agent(self, authed_client, query):
        """Rehab-domain queries must be dispatched to the rehab agent."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200
            result = consume_sse(resp)

        assert result["agent_used"] == "rehab", (
            f"Expected rehab agent for query: '{query}'\n"
            f"Got: '{result['agent_used']}'"
        )

    def test_rehab_agent_answer_is_non_empty(self, authed_client):
        """The rehab agent must return a non-empty answer."""
        query = "Describe a rehabilitation protocol for patellar tendinopathy."
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            result = consume_sse(resp)

        assert result["answer"], "Rehab agent returned an empty answer"

    def test_rehab_agent_no_server_error(self, authed_client):
        """Rehab queries must never trigger a 5xx response."""
        query = "I have lower back pain after deadlifts. What should I do?"
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code < 500, (
                f"Rehab query caused server error ({resp.status_code})"
            )
