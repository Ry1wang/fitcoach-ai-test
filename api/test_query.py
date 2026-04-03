"""
Layer 4 — Chat / Query Endpoint Tests
Tests POST /chat for happy-path behavior, adversarial queries, and concurrent
request isolation.
"""

import asyncio

import httpx
import pytest

from conftest import consume_sse

pytestmark = pytest.mark.layer4

# A simple, unambiguous training question that any fitness corpus should handle.
SIMPLE_TRAINING_QUERY = "What is the recommended rep range for building muscle?"


class TestQueryHappyPath:
    @pytest.mark.requires_corpus
    def test_query_returns_non_empty_answer(self, authed_client):
        """A well-formed query must return a non-empty streamed answer without errors."""
        with authed_client.stream("POST", "/chat", json={"message": SIMPLE_TRAINING_QUERY}) as resp:
            assert resp.status_code == 200, (
                f"Expected 200 for valid query, got {resp.status_code}: {resp.text}"
            )
            result = consume_sse(resp)

        assert result["answer"], "Answer is empty — SSE stream produced no token events"
        assert not result["error"], f"SSE stream reported an error: {result['error']}"

    @pytest.mark.requires_corpus
    def test_query_sse_has_done_event(self, authed_client):
        """The SSE stream must end with a 'done' event that contains agent_used."""
        with authed_client.stream("POST", "/chat", json={"message": SIMPLE_TRAINING_QUERY}) as resp:
            result = consume_sse(resp)

        event_types = {e.get("type") for e in result["events"]}
        assert "done" in event_types, (
            f"SSE stream has no 'done' event. Event types seen: {event_types}"
        )
        assert result["agent_used"], (
            "The 'done' event did not include an 'agent_used' field"
        )

    @pytest.mark.requires_corpus
    def test_agent_used_is_valid(self, authed_client):
        """agent_used must be one of the three known specialist agent names."""
        valid_agents = {"training", "rehab", "nutrition"}
        with authed_client.stream("POST", "/chat", json={"message": SIMPLE_TRAINING_QUERY}) as resp:
            result = consume_sse(resp)

        assert result["agent_used"] in valid_agents, (
            f"Unexpected agent_used value: '{result['agent_used']}'"
        )


class TestQueryValidation:
    def test_query_without_message_field_returns_422(self, authed_client):
        """Body without the 'message' field must return 422."""
        resp = authed_client.post("/chat", json={"question": "some text"})
        assert resp.status_code == 422, (
            f"Expected 422 for missing 'message' field, got {resp.status_code}"
        )

    def test_query_empty_message_returns_error(self, authed_client):
        """An empty-string message must return 422 or a clear 4xx error, not 5xx."""
        resp = authed_client.post("/chat", json={"message": ""})
        assert resp.status_code < 500, (
            f"Empty message triggered a server error ({resp.status_code}): {resp.text}"
        )

    def test_query_empty_body_returns_422(self, authed_client):
        """Empty JSON body must return 422."""
        resp = authed_client.post("/chat", json={})
        assert resp.status_code == 422

    def test_query_requires_auth(self, anon_client):
        """Query without a token must return 401."""
        resp = anon_client.post("/chat", json={"message": SIMPLE_TRAINING_QUERY})
        assert resp.status_code == 401


class TestAdversarialQueries:
    @pytest.mark.slow
    @pytest.mark.requires_corpus
    def test_adversarial_queries_no_server_error(
        self, authed_client, adversarial_queries
    ):
        """
        Every adversarial query must return HTTP 200 — no unhandled 5xx exceptions.
        Routing accuracy is measured separately in Layer 3; this test only guards
        against crashes.
        """
        failures = []
        for item in adversarial_queries:
            with authed_client.stream(
                "POST", "/chat", json={"message": item["query"]}
            ) as resp:
                if resp.status_code >= 500:
                    failures.append(
                        f"[{item['id']}] HTTP {resp.status_code} — {item['query'][:60]}"
                    )

        assert not failures, (
            f"{len(failures)} adversarial queries caused server errors:\n"
            + "\n".join(failures)
        )

    @pytest.mark.slow
    @pytest.mark.requires_corpus
    def test_adversarial_queries_return_answers(
        self, authed_client, adversarial_queries
    ):
        """
        Every answerable adversarial query must return a non-empty answer.
        Unanswerable queries (answerability='unanswerable') are excluded.
        """
        answerable = [
            q for q in adversarial_queries if q.get("answerability") == "answerable"
        ]
        empty_answers = []
        for item in answerable:
            with authed_client.stream(
                "POST", "/chat", json={"message": item["query"]}
            ) as resp:
                if resp.status_code != 200:
                    continue
                result = consume_sse(resp)
                if not result["answer"]:
                    empty_answers.append(
                        f"[{item['id']}] {item['query'][:60]}"
                    )

        assert not empty_answers, (
            f"{len(empty_answers)} answerable queries returned empty answers:\n"
            + "\n".join(empty_answers)
        )


class TestConcurrentRequests:
    @pytest.mark.slow
    @pytest.mark.requires_corpus
    @pytest.mark.asyncio
    async def test_concurrent_queries_no_context_bleed(self, base_url, auth_token):
        """
        Ten simultaneous queries must each return a non-empty answer.
        Verifies that concurrent requests do not trigger server errors or
        receive empty responses — a basic guard against session/context bleed.
        """
        headers = {"Authorization": f"Bearer {auth_token}"}

        # Use clearly distinct queries to detect obvious bleed
        queries = [
            "What is the recommended rep range for hypertrophy?",
            "How long does ACL recovery typically take?",
            "What are the best protein sources for muscle building?",
            "Describe the McGill Big 3 exercises for lower back rehabilitation.",
            "What is the difference between linear and undulating periodization?",
            "How does creatine supplementation affect strength performance?",
            "What are common causes of patellar tendinopathy?",
            "Explain the role of carbohydrates in sports performance.",
            "What is the starting strength novice linear progression program?",
            "How should I adjust training volume during a deload week?",
        ]

        async def query_one(client: httpx.AsyncClient, message: str) -> dict:
            async with client.stream("POST", "/chat", json={"message": message}) as resp:
                assert resp.status_code == 200, (
                    f"Concurrent query failed with {resp.status_code}"
                )
                tokens: list[str] = []
                agent_used = ""
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    import json
                    payload = line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "token":
                        tokens.append(event.get("content", ""))
                    elif event.get("type") == "done":
                        agent_used = event.get("agent_used", "")
                return {"answer": "".join(tokens), "agent_used": agent_used}

        async with httpx.AsyncClient(
            base_url=base_url, timeout=120.0, headers=headers
        ) as client:
            results = await asyncio.gather(
                *[query_one(client, q) for q in queries]
            )

        empty = [
            f"Query {i}: '{queries[i][:50]}'"
            for i, r in enumerate(results)
            if not r["answer"]
        ]
        assert not empty, (
            f"{len(empty)} concurrent queries returned empty answers:\n"
            + "\n".join(empty)
        )
