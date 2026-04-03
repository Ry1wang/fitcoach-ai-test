#!/usr/bin/env python3
"""
Layer 1 Runner: Adversarial Query Execution
Prerequisite: run layer1_pre.py first to register the test user and upload all documents.
Python Version: 3.11+
"""

import json
import os
import sys
import time
from pathlib import Path
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL = os.getenv("FITCOACH_API_URL", "http://localhost/api/v1")
QUERY_FILE   = Path(__file__).parent.parent / "ai_generated/adversarial_queries.json"

TEST_USER = {"username": "test_runner", "email": "test_runner@example.com", "password": "TestPassword123!"}

# Colors
GREEN, YELLOW, RED, BLUE, BOLD, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[94m", "\033[1m", "\033[0m"

READY_STATUSES = ("completed", "processed", "ready")


class FitCoachClient:
    def __init__(self):
        self.client = httpx.Client(base_url=API_BASE_URL, timeout=120.0)
        self.token = None

    def ensure_auth(self):
        print(f"{BLUE}Authenticating as {TEST_USER['username']}...{RESET}")
        try:
            resp = self.client.post(
                "/auth/login",
                data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
            )
            resp.raise_for_status()
            self.token = resp.json()["access_token"]
            self.client.headers.update({"Authorization": f"Bearer {self.token}"})
            print(f"{GREEN}✓ Authenticated successfully.{RESET}")
        except Exception as e:
            print(f"{RED}Auth failure: {e}{RESET}")
            print(f"{YELLOW}Hint: run layer1_pre.py first to register the test user.{RESET}")
            sys.exit(1)

    def check_corpus_ready(self) -> None:
        """Abort if any corpus document is not yet in a ready state."""
        try:
            docs = self.client.get("/documents").json().get("documents", [])
        except Exception as e:
            print(f"{RED}Could not fetch document list: {e}{RESET}")
            sys.exit(1)

        not_ready = [d for d in docs if d.get("status") not in READY_STATUSES]
        if not_ready:
            print(f"{RED}Corpus not ready — {len(not_ready)} document(s) are still processing or failed:{RESET}")
            for d in not_ready:
                print(f"  - {d['filename']} (status={d.get('status')})")
            print(f"{YELLOW}Run layer1_pre.py and wait for it to finish before running this script.{RESET}")
            sys.exit(1)

        print(f"{GREEN}✓ {len(docs)} document(s) ready. Starting queries.{RESET}")

    def query(self, text: str) -> dict:
        """Query with Language Hint to ensure response matches query language."""
        language_hint = "\n\n(Important: Please respond strictly in the same language as my query.)"
        augmented_message = text + language_hint

        tokens: list[str] = []
        agent_used: str = ""

        with self.client.stream("POST", "/chat", json={"message": augmented_message}) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                    if event.get("type") == "token":
                        tokens.append(event.get("content", ""))
                    elif event.get("type") == "done":
                        agent_used = event.get("agent_used", "")
                except json.JSONDecodeError:
                    continue

        return {
            "answer": "".join(tokens),
            "agents_involved": [agent_used] if agent_used else [],
        }


# ---------------------------------------------------------------------------
# Runner Logic
# ---------------------------------------------------------------------------

def main():
    if not QUERY_FILE.exists():
        print(f"{RED}Error: Run generate_cases.py first.{RESET}")
        sys.exit(1)

    with open(QUERY_FILE, "r", encoding="utf-8") as f:
        queries = json.load(f)

    fc = FitCoachClient()
    fc.ensure_auth()
    fc.check_corpus_ready()

    print(f"\n{BOLD}{BLUE}=== Starting Adversarial Test Execution ==={RESET}\n")
    stats = {"passed": 0, "failed": 0, "errors": 0}

    for i, item in enumerate(queries):
        print(f"{BOLD}[{item['id']}] {item['category']}{RESET}")
        print(f"{YELLOW}Q: {item['query']}{RESET}")

        start_time = time.time()
        try:
            res = fc.query(item["query"])
            duration = time.time() - start_time
            answer = res.get("answer", "No answer")
            actual_agents = res.get("agents_involved", [])
            print(f"{GREEN}A: {answer[:300]}...{RESET}")

            match = set(actual_agents) == set(item["expected_agents"])
            if match:
                print(f"  Result: {GREEN}PASS{RESET} ({duration:.2f}s)")
                stats["passed"] += 1
            else:
                print(f"  Result: {RED}MISMATCH{RESET} (Expected: {item['expected_agents']}, Actual: {actual_agents})")
                stats["failed"] += 1
        except Exception as e:
            print(f"  Result: {RED}API ERROR{RESET} ({e})")
            stats["errors"] += 1
        print("-" * 40)

    print(f"\n{BOLD}Final Summary:{RESET}")
    print(f"  Passed: {stats['passed']} | Failed: {stats['failed']} | Errors: {stats['errors']}")
    if queries:
        print(f"  Success Rate: {(stats['passed']/len(queries))*100:.1f}%")


if __name__ == "__main__":
    main()
