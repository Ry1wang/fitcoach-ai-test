"""
Layer 4 — Nutrition Agent Endpoint Tests
Verifies that nutrition-domain queries are answered by the nutrition specialist agent.
"""

import pytest

from conftest import consume_sse

pytestmark = [pytest.mark.layer4, pytest.mark.requires_corpus]

NUTRITION_QUERIES = [
    "Is creatine supplementation safe for long-term use?",
    "What is the recommended daily protein intake for strength athletes?",
    "How do carbohydrates affect athletic performance?",
]


class TestNutritionAgent:
    @pytest.mark.parametrize("query", NUTRITION_QUERIES)
    def test_nutrition_query_selects_nutrition_agent(self, authed_client, query):
        """Nutrition-domain queries must be dispatched to the nutrition agent."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200
            result = consume_sse(resp)

        assert result["agent_used"] == "nutrition", (
            f"Expected nutrition agent for query: '{query}'\n"
            f"Got: '{result['agent_used']}'"
        )

    def test_nutrition_agent_answer_is_non_empty(self, authed_client):
        """The nutrition agent must return a non-empty answer."""
        query = "What are the best dietary strategies for muscle recovery after training?"
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            result = consume_sse(resp)

        assert result["answer"], "Nutrition agent returned an empty answer"

    def test_nutrition_agent_no_server_error(self, authed_client):
        """Nutrition queries must never trigger a 5xx response."""
        query = "What is the evidence for BCAAs supplementation?"
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code < 500, (
                f"Nutrition query caused server error ({resp.status_code})"
            )
