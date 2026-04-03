#!/usr/bin/env python3
"""
Layer 1 Pre-Flight: User Registration & Document Upload
Run this once before layer1_runner.py to ensure the test user exists
and all corpus documents are uploaded and fully indexed.
"""

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
CORPUS_DIR   = Path(__file__).parent.parent / "fixtures/real_world"

TEST_USER = {
    "username": "test_runner",
    "email":    "test_runner@example.com",
    "password": "TestPassword123!",
}

UPLOAD_TIMEOUT     = httpx.Timeout(connect=10.0, write=300.0, read=300.0, pool=10.0)
UPLOAD_MAX_RETRIES = 3
UPLOAD_RETRY_DELAY = 5   # seconds between retries on 5xx

INDEX_POLL_INTERVAL = 10  # seconds between indexing status checks

READY_STATUSES    = ("completed", "processed", "ready")
TERMINAL_STATUSES = READY_STATUSES + ("failed",)

# Colors
GREEN, YELLOW, RED, BLUE, BOLD, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[94m", "\033[1m", "\033[0m"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _domain_for(pdf: Path) -> str:
    name = pdf.name.lower()
    if "nutrition" in name or "food" in name:
        return "nutrition"
    if "rehab" in name or "milo" in name or "supple" in name:
        return "rehab"
    return "training"


def ensure_auth(client: httpx.Client) -> None:
    print(f"{BLUE}Authenticating as {TEST_USER['username']}...{RESET}")
    resp = client.post(
        "/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
    )
    if resp.status_code == 401:
        print("  User not found — registering...")
        reg = client.post("/auth/register", json=TEST_USER)
        if reg.status_code not in (200, 201):
            print(f"{RED}  Registration failed (HTTP {reg.status_code}): {reg.text}{RESET}")
            sys.exit(1)
        print(f"{GREEN}  ✓ Registered.{RESET}")
        resp = client.post(
            "/auth/login",
            data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        )

    resp.raise_for_status()
    token = resp.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    print(f"{GREEN}✓ Authenticated successfully.{RESET}")


def upload_pdf(client: httpx.Client, pdf: Path) -> None:
    domain = _domain_for(pdf)
    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        with open(pdf, "rb") as f:
            resp = client.post(
                "/documents/upload",
                files={"file": (pdf.name, f, "application/pdf")},
                data={"domain": domain},
                timeout=UPLOAD_TIMEOUT,
            )
        if resp.status_code < 500:
            resp.raise_for_status()
            print(f"  {GREEN}✓ Uploaded:{RESET} {pdf.name} (domain={domain})")
            return
        print(
            f"  {YELLOW}⚠ Attempt {attempt}/{UPLOAD_MAX_RETRIES} got HTTP {resp.status_code},"
            f" retrying in {UPLOAD_RETRY_DELAY}s...{RESET}"
        )
        if attempt < UPLOAD_MAX_RETRIES:
            time.sleep(UPLOAD_RETRY_DELAY)

    raise RuntimeError(
        f"Upload failed after {UPLOAD_MAX_RETRIES} attempts (last status: {resp.status_code})"
    )


def delete_document(client: httpx.Client, doc_id: str, filename: str) -> None:
    resp = client.delete(f"/documents/{doc_id}")
    if resp.status_code not in (200, 204):
        print(f"  {YELLOW}⚠ Could not delete {filename} (HTTP {resp.status_code}){RESET}")
    else:
        print(f"  {YELLOW}↺ Removed stuck document: {filename}{RESET}")


def _wait_for_file_ready(client: httpx.Client, filename: str) -> None:
    """Poll until the named document reaches a terminal status."""
    print(f"  {BLUE}Waiting for '{filename}' to finish indexing...{RESET}")
    start = time.monotonic()
    while True:
        time.sleep(INDEX_POLL_INTERVAL)
        try:
            docs = client.get("/documents").json().get("documents", [])
        except Exception:
            print(f"    {YELLOW}[warn] Could not parse /documents response, retrying...{RESET}")
            continue

        match = next((d for d in docs if d["filename"] == filename), None)
        if match is None:
            print(f"    {YELLOW}[warn] '{filename}' not found in document list yet...{RESET}")
            continue

        status  = match.get("status", "")
        elapsed = int(time.monotonic() - start)

        if status in READY_STATUSES:
            print(f"  {GREEN}✓ '{filename}' ready ({elapsed}s){RESET}")
            return
        if status == "failed":
            msg = match.get("error_message", "unknown error")
            print(f"  {RED}✗ '{filename}' failed after {elapsed}s: {msg}{RESET}")
            return

        print(f"    [{elapsed:>3}s] status={status}")


def sync_corpus(client: httpx.Client) -> None:
    """
    1. Delete any documents stuck in processing.
    2. Upload any local PDFs not yet present on the server.
    3. Block until every corpus document reaches a terminal status.
    """
    print(f"\n{BLUE}Syncing document corpus...{RESET}")

    local_pdfs  = sorted(CORPUS_DIR.glob("*.pdf"))
    if not local_pdfs:
        print(f"{RED}Error: No PDFs found in {CORPUS_DIR}{RESET}")
        sys.exit(1)
    local_names = {p.name for p in local_pdfs}

    # --- Step 1: clear stuck documents ---
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        remote_docs = client.get("/documents").json().get("documents", [])
    except Exception:
        remote_docs = []
    stuck = []
    for d in remote_docs:
        if d.get("status") == "processing":
            ts = d.get("updated_at", "").replace("Z", "+00:00")
            try:
                updated_at = datetime.fromisoformat(ts).replace(tzinfo=None)
            except ValueError:
                updated_at = now
            elapsed = (now - updated_at).total_seconds()
            if d.get("created_at") == d.get("updated_at") or elapsed > 180:
                stuck.append(d)

    if stuck:
        print(f"{YELLOW}Found {len(stuck)} stuck document(s). Removing...{RESET}")
        for d in stuck:
            delete_document(client, d["id"], d["filename"])
        try:
            remote_docs = client.get("/documents").json().get("documents", [])
        except Exception:
            remote_docs = []

    # --- Step 2: upload missing files one at a time, waiting for each to be ready ---
    remote_names = {d["filename"] for d in remote_docs}
    missing = [p for p in local_pdfs if p.name not in remote_names]

    if not missing:
        print(f"{GREEN}✓ All local files already present on server.{RESET}")
    else:
        print(f"{YELLOW}Uploading {len(missing)} missing file(s) (one at a time)...{RESET}")
        for pdf in missing:
            try:
                upload_pdf(client, pdf)
            except Exception as e:
                print(f"  {RED}✗ Failed to upload {pdf.name}: {e}{RESET}")
                continue
            _wait_for_file_ready(client, pdf.name)

    # --- Step 3: verify all corpus docs are ready ---
    print(f"\n{BLUE}Verifying all {len(local_names)} document(s) are ready...{RESET}")
    start = time.monotonic()

    while True:
        try:
            docs = client.get("/documents").json().get("documents", [])
        except Exception:
            print(f"  {YELLOW}[warn] Could not parse /documents response, retrying...{RESET}")
            time.sleep(INDEX_POLL_INTERVAL)
            continue

        corpus_docs = [d for d in docs if d["filename"] in local_names]

        ready   = [d for d in corpus_docs if d.get("status") in READY_STATUSES]
        failed  = [d for d in corpus_docs if d.get("status") == "failed"]
        pending = [d for d in corpus_docs if d.get("status") not in TERMINAL_STATUSES]

        elapsed = int(time.monotonic() - start)
        print(
            f"  [{elapsed:>3}s] "
            f"{GREEN}Ready: {len(ready)}{RESET} | "
            f"{RED}Failed: {len(failed)}{RESET} | "
            f"{YELLOW}Pending: {len(pending)}{RESET}"
        )

        if failed:
            for d in failed:
                print(f"    {RED}✗ {d['filename']}: {d.get('error_message', 'unknown error')}{RESET}")

        if not pending:
            break

        time.sleep(INDEX_POLL_INTERVAL)

    total_ready = len(ready)
    total_local = len(local_names)
    if total_ready == total_local:
        print(f"\n{GREEN}{BOLD}✓ All {total_ready} document(s) ready. Pre-flight complete.{RESET}")
    else:
        print(
            f"\n{YELLOW}Pre-flight complete with issues: "
            f"{total_ready}/{total_local} documents ready.{RESET}"
        )
        if failed:
            print(f"{RED}Fix the failed documents before running layer1_runner.py.{RESET}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    client = httpx.Client(base_url=API_BASE_URL, timeout=120.0)
    try:
        ensure_auth(client)
        sync_corpus(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
