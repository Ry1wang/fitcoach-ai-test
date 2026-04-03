"""
Shared pytest fixtures for FitCoach AI test suite.
All layers can import these fixtures by declaring them as function parameters.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = os.getenv("FITCOACH_API_URL", "http://localhost/api/v1")

TEST_USER = {
    "username": "test_runner",
    "email": "test_runner@example.com",
    "password": "TestPassword123!",
}

# A minimal structurally-valid PDF (contains no readable text — upload/parse only)
MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"startxref\n190\n%%EOF\n"
)

REAL_WORLD_DIR = Path(__file__).parent / "fixtures" / "real_world"

# ---------------------------------------------------------------------------
# CLI option
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--base-url",
        action="store",
        default=_DEFAULT_BASE_URL,
        help="Base URL of the FitCoach API (default: FITCOACH_API_URL env or http://localhost/api/v1)",
    )


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--base-url")


@pytest.fixture(scope="session")
def anon_client(base_url: str):
    """Unauthenticated HTTP client — for testing 401 responses."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def auth_token(base_url: str) -> str:
    """
    Returns a valid bearer token for TEST_USER.
    Registers the user first if it does not exist yet.
    Scoped to the session so authentication happens only once.
    """
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        resp = client.post(
            "/auth/login",
            data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        )
        if resp.status_code == 401:
            reg = client.post("/auth/register", json=TEST_USER)
            assert reg.status_code in (200, 201), (
                f"Test-user registration failed ({reg.status_code}): {reg.text}"
            )
            resp = client.post(
                "/auth/login",
                data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
            )
        resp.raise_for_status()
        return resp.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="session")
def authed_client(base_url: str, auth_token: str):
    """Authenticated HTTP client — used by most Layer 4 tests."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    with httpx.Client(base_url=base_url, timeout=120.0, headers=headers) as client:
        yield client


# ---------------------------------------------------------------------------
# File / corpus fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_pdf() -> bytes:
    """Minimal valid PDF bytes — good for upload/pipeline tests."""
    return MINIMAL_PDF_BYTES


@pytest.fixture(scope="session")
def small_real_pdf() -> Path:
    """
    Path to the smallest real-world corpus PDF available locally.
    Skips the test if no PDFs are present (corpus not set up on this machine).
    """
    pdfs = sorted(REAL_WORLD_DIR.glob("*.pdf"), key=lambda p: p.stat().st_size)
    if not pdfs:
        pytest.skip("No real-world corpus PDFs found; run layer1_pre.py first.")
    return pdfs[0]


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def consume_sse(response: httpx.Response) -> dict:
    """
    Consume an SSE streaming response and return a summary dict::

        {
            "answer": "<full concatenated text>",
            "agent_used": "<agent name from done event>",
            "events": [<raw parsed event dicts>],
            "error": "<error message if an error event was received>",
        }

    Caller is responsible for using ``client.stream()`` context manager.
    """
    tokens: list[str] = []
    agent_used = ""
    events: list[dict] = []
    error_msg = ""

    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        events.append(event)
        etype = event.get("type", "")

        if etype == "token":
            tokens.append(event.get("content", ""))
        elif etype == "done":
            agent_used = event.get("agent_used", "")
        elif etype == "error":
            error_msg = event.get("message", event.get("content", "unknown error"))

    return {
        "answer": "".join(tokens),
        "agent_used": agent_used,
        "events": events,
        "error": error_msg,
    }


# ---------------------------------------------------------------------------
# Adversarial query data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def adversarial_queries() -> list[dict]:
    """Load the versioned adversarial query bank from Layer 1."""
    query_file = Path(__file__).parent / "ai_generated" / "adversarial_queries.json"
    if not query_file.exists():
        pytest.skip("adversarial_queries.json not found; run generate_cases.py first.")
    with open(query_file, encoding="utf-8") as f:
        return json.load(f)
