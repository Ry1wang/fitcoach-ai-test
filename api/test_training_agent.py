"""
Layer 4 — Training Agent Endpoint Tests
Verifies that training-domain queries are answered with training-relevant content
and that the training agent is selected.
"""

import pytest

from conftest import consume_sse

pytestmark = [pytest.mark.layer4, pytest.mark.requires_corpus]

TRAINING_QUERIES = [
    "What rep range is best for hypertrophy training?",
    "Explain the starting strength linear progression model.",
    "How does periodization improve strength gains over time?",
]


class TestTrainingAgent:
    @pytest.mark.parametrize("query", TRAINING_QUERIES)
    def test_training_query_selects_training_agent(self, authed_client, query):
        """Training-domain queries must be dispatched to the training agent."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200
            result = consume_sse(resp)

        assert result["agent_used"] == "training", (
            f"Expected training agent for query: '{query}'\n"
            f"Got: '{result['agent_used']}'"
        )

    def test_training_agent_answer_is_non_empty(self, authed_client):
        """The training agent must return a non-empty answer."""
        query = "What is the correct technique for the deadlift?"
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            result = consume_sse(resp)

        assert result["answer"], "Training agent returned an empty answer"

    def test_training_agent_no_server_error(self, authed_client):
        """Training queries must never trigger a 5xx response."""
        query = "Describe a 5x5 strength training program."
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code < 500, (
                f"Training query caused server error ({resp.status_code})"
            )
