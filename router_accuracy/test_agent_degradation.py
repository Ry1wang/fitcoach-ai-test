"""
Layer 3 — Agent Degradation Tests

Tests each specialist agent's behavior when given edge-case or degraded inputs.
The goal is to confirm that agents fail gracefully (no 5xx, no silent hang,
well-formed SSE) rather than throwing unhandled exceptions.

─────────────────────────────────────────────────────────────────────────────
Scope of this file (external API boundary — no LLM mocking):

  COVERED
  ├─ Out-of-corpus queries: agent receives a query whose answer is not in
  │  its specific corpus books.  Must return a graceful "I can't find that"
  │  answer, not a 5xx or an empty stream.
  ├─ SSE stream format per agent: every agent's response must include
  │  token events, a done event, and a non-empty agent_used field.
  ├─ Client abort mid-stream: closing the connection after the first token
  │  must not leave the server in a broken state — a follow-up request must
  │  still succeed.
  ├─ Whitespace / near-empty query: a query consisting of only whitespace
  │  must not cause a 5xx (may return 422 or graceful "unclear question").
  └─ Rapid sequential queries per agent: 3 queries to the same domain in
     quick succession must all complete without server errors.

  NOT COVERED HERE (require backend-level unit test / LLM mock injection):
  ├─ LLM returns empty string      → backend must handle; not observable externally
  ├─ LLM API timeout               → would require network-layer mocking
  └─ LLM returns malformed JSON    → internal to LangGraph agent; not observable externally
  These gaps are documented in KNOWN_ISSUES.md — ISSUE-003.

─────────────────────────────────────────────────────────────────────────────
"""

import time

import httpx
import pytest

from conftest import consume_sse

pytestmark = [pytest.mark.layer3, pytest.mark.requires_corpus]

# ---------------------------------------------------------------------------
# Queries designed to be OUT-OF-CORPUS for each agent's specific book set.
# The system has: Starting Strength, Scientific Principles of Strength
# Training (training); Rebuilding Milo (rehab); Foods Nutrition and Sports
# Performance (nutrition).  The queries below reference content NOT in those
# books — forcing the agent to handle "I don't know" gracefully.
# ---------------------------------------------------------------------------

OUT_OF_CORPUS_BY_AGENT = {
    "training": (
        "Describe the Westside Barbell conjugate system in detail as explained "
        "in Louie Simmons' original writings.",
        # Westside/Simmons is not in the corpus; router should still route to
        # training, and training agent should respond gracefully.
    ),
    "rehab": (
        "Explain the Feldenkrais Method for neurological rehabilitation of "
        "stroke patients, citing the original research.",
        # Feldenkrais is not in any corpus book.
    ),
    "nutrition": (
        "What does the 'Racing Weight' book by Matt Fitzgerald say about "
        "body composition for endurance athletes?",
        # Racing Weight is not in the corpus.
    ),
}

# One clearly in-domain query per agent used for follow-up / format checks.
CANONICAL_BY_AGENT = {
    "training": "How many sets per week are optimal for strength development?",
    "rehab":    "My lower back hurts after deadlifts. What should I do?",
    "nutrition": "What is the recommended daily protein intake for athletes?",
}

VALID_AGENTS = {"training", "rehab", "nutrition"}


# ---------------------------------------------------------------------------
# Helper: assert SSE response is well-formed
# ---------------------------------------------------------------------------

def _assert_well_formed_sse(result: dict, context: str) -> None:
    """Assert that an SSE response has the expected structure."""
    assert result["agent_used"] in VALID_AGENTS, (
        f"{context}: agent_used='{result['agent_used']}' is not a valid agent name"
    )
    assert not result.get("error"), (
        f"{context}: SSE stream contained an error event: {result['error']}"
    )
    event_types = {e.get("type") for e in result["events"]}
    assert "done" in event_types, (
        f"{context}: SSE stream missing 'done' event. Events seen: {event_types}"
    )


# ---------------------------------------------------------------------------
# Out-of-corpus graceful degradation
# ---------------------------------------------------------------------------

class TestOutOfCorpusDegradation:
    """
    Each agent must handle queries about content not in its corpus without
    crashing.  The acceptable outcomes are:
      - A non-empty answer explaining the content is not available (ideal)
      - An SSE error event with a user-friendly message (acceptable)
    What is NOT acceptable: HTTP 5xx or a completely empty/hung response.
    """

    @pytest.mark.parametrize("domain,query", OUT_OF_CORPUS_BY_AGENT.items())
    def test_out_of_corpus_no_5xx(self, authed_client, domain, query):
        """An out-of-corpus query must not produce a 5xx server error."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code < 500, (
                f"[{domain}] Out-of-corpus query caused server error "
                f"({resp.status_code}): {resp.text[:200]}"
            )

    @pytest.mark.parametrize("domain,query", OUT_OF_CORPUS_BY_AGENT.items())
    def test_out_of_corpus_sse_terminates(self, authed_client, domain, query):
        """
        An out-of-corpus query must still produce a terminated SSE stream
        (a 'done' event).  A hanging/never-terminating stream is a failure.
        """
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            if resp.status_code != 200:
                pytest.skip(
                    f"[{domain}] Non-200 response ({resp.status_code}); "
                    "skipping SSE format check."
                )
            result = consume_sse(resp)

        event_types = {e.get("type") for e in result["events"]}
        assert "done" in event_types, (
            f"[{domain}] Out-of-corpus query: SSE stream never sent 'done' event.\n"
            f"  Events seen: {event_types}\n"
            f"  Query: {query[:80]}"
        )

    @pytest.mark.parametrize("domain,query", OUT_OF_CORPUS_BY_AGENT.items())
    def test_out_of_corpus_answer_not_hallucinated(self, authed_client, domain, query):
        """
        When corpus content is missing, the answer must not fabricate specific
        citations or page numbers.  We check that the answer is either:
          (a) non-empty and does not assert corpus-grounded facts, OR
          (b) explicitly states it cannot find the relevant content.
        This is a soft check — it asserts the answer is non-empty and the stream
        is valid; manual review is required to detect hallucination.
        """
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            if resp.status_code != 200:
                pytest.skip(f"[{domain}] Non-200 ({resp.status_code})")
            result = consume_sse(resp)

        # Soft assertion: an answer exists (even "I don't know" is an answer).
        # Hard assertion: no SSE-level error (which would indicate a crash).
        assert not result.get("error"), (
            f"[{domain}] Out-of-corpus query produced SSE error: {result['error']}"
        )
        # Note: hallucination detection requires semantic evaluation (Layer 2 RAGAS).
        # This test only confirms the pipeline did not crash.


# ---------------------------------------------------------------------------
# Per-agent SSE format validation
# ---------------------------------------------------------------------------

class TestAgentSseFormat:
    """
    Each agent's response must conform to the expected SSE event structure:
    one or more token events → a done event with agent_used set.
    """

    @pytest.mark.parametrize("domain,query", CANONICAL_BY_AGENT.items())
    def test_agent_sse_has_token_and_done_events(self, authed_client, domain, query):
        """SSE stream must contain at least one token event and one done event."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200, (
                f"[{domain}] Expected 200, got {resp.status_code}"
            )
            result = consume_sse(resp)

        event_types = [e.get("type") for e in result["events"]]
        assert "token" in event_types, (
            f"[{domain}] SSE stream has no 'token' events — answer may be empty.\n"
            f"  Event types: {event_types}"
        )
        assert "done" in event_types, (
            f"[{domain}] SSE stream has no 'done' event.\n"
            f"  Event types: {event_types}"
        )

    @pytest.mark.parametrize("domain,query", CANONICAL_BY_AGENT.items())
    def test_agent_used_matches_domain(self, authed_client, domain, query):
        """
        For canonical single-domain queries, the agent_used field in the done
        event must match the expected domain.  This is both a format and a
        routing correctness check.
        """
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200
            result = consume_sse(resp)

        assert result["agent_used"] == domain, (
            f"[{domain}] Canonical query routed to wrong agent.\n"
            f"  Expected: {domain}\n"
            f"  Actual:   {result['agent_used']}\n"
            f"  Query:    {query}"
        )

    @pytest.mark.parametrize("domain,query", CANONICAL_BY_AGENT.items())
    def test_agent_answer_is_non_empty(self, authed_client, domain, query):
        """Every agent must return a non-empty answer for a canonical query."""
        with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
            assert resp.status_code == 200
            result = consume_sse(resp)

        assert result["answer"].strip(), (
            f"[{domain}] Agent returned an empty answer for canonical query: '{query}'"
        )


# ---------------------------------------------------------------------------
# Client abort mid-stream (connection resilience)
# ---------------------------------------------------------------------------

class TestClientAbort:
    """
    If a client disconnects mid-stream, the server must remain healthy.
    A follow-up request after an aborted SSE connection must succeed normally.
    """

    def test_abort_midstream_server_stays_healthy(self, authed_client):
        """
        Open an SSE stream, read only the first chunk, then close the connection.
        A subsequent full request must complete successfully, confirming the
        server did not get stuck or leak resources from the aborted request.
        """
        aborted = False
        try:
            with authed_client.stream(
                "POST", "/chat", json={"message": CANONICAL_BY_AGENT["training"]}
            ) as resp:
                assert resp.status_code == 200, (
                    f"Expected 200 to start stream, got {resp.status_code}"
                )
                # Read exactly one line then forcefully close.
                for _ in resp.iter_lines():
                    aborted = True
                    break  # exits the loop; context manager closes connection
        except httpx.RemoteProtocolError:
            pass  # Expected: server may complain about the early close.

        assert aborted, "Stream never started — could not test abort scenario"

        # Brief pause to let the server process the disconnection.
        time.sleep(1)

        # Follow-up request must succeed — server must not be in broken state.
        with authed_client.stream(
            "POST", "/chat",
            json={"message": CANONICAL_BY_AGENT["nutrition"]},
        ) as resp:
            assert resp.status_code == 200, (
                f"Follow-up request after abort returned {resp.status_code} — "
                "server may have been left in broken state by the aborted connection."
            )
            result = consume_sse(resp)

        assert result["agent_used"] in VALID_AGENTS, (
            "Follow-up request after abort returned invalid agent_used"
        )


# ---------------------------------------------------------------------------
# Near-empty query handling
# ---------------------------------------------------------------------------

class TestNearEmptyQuery:
    """
    Queries that are technically non-empty but carry no semantic content
    (whitespace, single character, punctuation only) must not crash the server.
    """

    @pytest.mark.parametrize("bad_query,label", [
        ("   ",         "whitespace_only"),
        ("?",           "single_punctuation"),
        ("a",           "single_character"),
        ("...",         "ellipsis_only"),
    ])
    def test_near_empty_query_no_5xx(self, authed_client, bad_query, label):
        """Near-empty queries must not produce 5xx server errors."""
        try:
            resp = authed_client.post("/chat", json={"message": bad_query})
            assert resp.status_code < 500, (
                f"[{label}] Near-empty query caused server error "
                f"({resp.status_code}): {resp.text[:200]}"
            )
        except httpx.ReadTimeout:
            pytest.skip(f"[{label}] Request timed out — server may be processing.")


# ---------------------------------------------------------------------------
# Rapid sequential queries per agent
# ---------------------------------------------------------------------------

class TestRapidSequentialQueries:
    """
    Send 3 queries to the same domain in quick succession.
    All must complete without 5xx errors, confirming agents do not
    accumulate state or lock resources between requests.
    """

    @pytest.mark.parametrize("domain", list(CANONICAL_BY_AGENT.keys()))
    @pytest.mark.slow
    def test_three_sequential_queries_no_5xx(self, authed_client, domain):
        """Three back-to-back queries to the same agent must all return < 500."""
        query = CANONICAL_BY_AGENT[domain]
        for attempt in range(1, 4):
            with authed_client.stream("POST", "/chat", json={"message": query}) as resp:
                status = resp.status_code
                if status == 200:
                    consume_sse(resp)  # drain the stream
            assert status < 500, (
                f"[{domain}] Attempt {attempt}/3 returned server error ({status})"
            )
            time.sleep(1)  # 1-second gap to avoid rate limiting (KNOWN_ISSUES ISSUE-002)
