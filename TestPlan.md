# FitCoach AI — Test Plan

**Version:** 1.0  
**Date:** 2026-04-02  
**Status:** Active  

---

## Table of Contents

- [FitCoach AI — Test Plan](#fitcoach-ai--test-plan)
  - [Table of Contents](#table-of-contents)
  - [1. Background](#1-background)
  - [2. Why the Original Tests Failed](#2-why-the-original-tests-failed)
  - [3. Design Concepts](#3-design-concepts)
    - [3.1 Separate Test Repository](#31-separate-test-repository)
    - [3.2 AI as Test Designer, Not Test Runner](#32-ai-as-test-designer-not-test-runner)
    - [3.3 Five-Layer Defense](#33-five-layer-defense)
    - [3.4 Golden Dataset as Regression Anchor](#34-golden-dataset-as-regression-anchor)
    - [3.5 Deterministic Assertions Where Possible, Semantic Where Not](#35-deterministic-assertions-where-possible-semantic-where-not)
  - [4. Repository Structure](#4-repository-structure)
  - [5. Five-Layer Defense Architecture](#5-five-layer-defense-architecture)
    - [Layer 1 — AI-Generated Adversarial Test Cases](#layer-1--ai-generated-adversarial-test-cases)
    - [Layer 2 — RAG Quality Evaluation](#layer-2--rag-quality-evaluation)
    - [Layer 3 — Router \& Agent Logic Testing](#layer-3--router--agent-logic-testing)
    - [Layer 4 — API Integration Testing](#layer-4--api-integration-testing)
    - [Layer 5 — Playwright E2E UI Testing](#layer-5--playwright-e2e-ui-testing)
  - [6. Execution Environment](#6-execution-environment)
  - [7. AI Involvement Model](#7-ai-involvement-model)
  - [8. Test Data Management](#8-test-data-management)
  - [9. Reporting](#9-reporting)
  - [10. Expansion Roadmap](#10-expansion-roadmap)

---

## 1. Background

**Project:** FitCoach AI — a RAG-powered multi-agent fitness knowledge assistant.

**Architecture summary:**

- Users upload PDF fitness books via a React web frontend
- A FastAPI backend parses PDFs, chunks content, and stores embeddings in PostgreSQL + pgvector
- On query, a LangGraph Supervisor orchestrates a Router Agent that classifies the intent and dispatches to one of three specialist agents: Training, Rehabilitation, or Nutrition
- Each specialist agent performs vector retrieval against its corresponding document set and returns grounded answers
- Redis handles session state and caching
- The entire stack is containerized with Docker
- A Feishu Bot integration via OpenClaw provides an additional interface channel

**Technology stack:**

| Component | Technology |
|---|---|
| Backend API | FastAPI |
| Frontend | React |
| Agent Framework | LangGraph (Supervisor-Worker) |
| Vector Store | PostgreSQL + pgvector |
| Cache / State | Redis |
| Infrastructure | Docker Compose |
| Bot Integration | OpenClaw → Feishu |

---

## 2. Why the Original Tests Failed

During development, Claude was used to write test cases at each stage, and all of them passed. However, real-world usage surfaced meaningful bugs. The root cause is a set of structural gaps between AI-generated tests and production behavior.

**Gap 1 — Happy-path bias.** AI-generated tests, when given no explicit adversarial instruction, default to well-formed inputs: grammatically clean questions, correct file types, standard request formats. Real users send ambiguous multi-domain questions, upload incorrect files, and phrase queries in ways no developer would anticipate.

**Gap 2 — RAG quality was never measured.** Whether the vector retrieval actually returned relevant chunks, and whether the LLM answer was faithful to those chunks rather than hallucinated, was never tested. Unit tests on the chunking pipeline confirmed it ran without error — not that it produced useful results.

**Gap 3 — Router correctness was assumed.** The Router Agent was tested with clear, unambiguous queries. Boundary cases — questions that span two domains, questions with no clear fitness intent, questions in mixed languages — were never evaluated for routing accuracy.

**Gap 4 — No UI-level verification.** The web frontend was never covered by automated tests. Visual regressions, broken upload flows, and error-state rendering failures could only be caught by manual inspection.

**Gap 5 — No regression baseline.** Without a fixed golden dataset and deterministic comparison, there was no way to know whether a code change silently degraded answer quality.

---

## 3. Design Concepts

### 3.1 Separate Test Repository

Tests live in their own GitHub repository (`fitcoach-ai-tests`), independent of the application codebase. This provides:

- Independent versioning of test suites and application releases
- Clean pull request history: test changes do not pollute feature commits
- The test repo can be updated without requiring an application deployment

### 3.2 AI as Test Designer, Not Test Runner

Claude (and other LLMs) are used during the **design phase** — generating adversarial query sets, interpreting failure reports, and suggesting new test cases. They are not involved in mechanical test execution, which is handled by deterministic shell scripts. This separation makes the pipeline reliable regardless of any LLM tool-calling issues.

Specifically, LLM involvement during design covers:

- Reading the FastAPI OpenAPI schema to understand all endpoints and their request/response contracts, then generating API test cases for Layer 4
- Generating adversarial, ambiguous, and edge-case queries for Layer 1
- Interpreting pytest JSON reports to surface root-cause hypotheses

### 3.3 Five-Layer Defense

Each layer targets a different failure mode. Layers are implemented incrementally — a shaky foundation in earlier layers makes upper layers meaningless.

| Layer | What it guards | Primary tool |
|---|---|---|
| L1 — Adversarial cases | Input-level failure modes | Claude (generation), pytest |
| L2 — RAG quality | Retrieval relevance, hallucination | RAGAS |
| L3 — Router accuracy | Agent misclassification | pytest + labeled dataset |
| L4 — API integration | Endpoint contracts, pipeline integrity | pytest + httpx |
| L5 — UI E2E | User-facing flows, visual regressions | Playwright |

### 3.4 Golden Dataset as Regression Anchor

A curated set of fixed PDF books, fixed queries, and human-verified expected answers forms the regression baseline. Any code change that causes answer quality to fall below threshold on this dataset is flagged immediately, even if all unit tests pass.

### 3.5 Deterministic Assertions Where Possible, Semantic Where Not

For API responses with structured output (status codes, JSON schema, field presence), use exact assertions. For free-text LLM responses, use semantic similarity scoring rather than string matching — an answer that conveys the same meaning with different wording should not fail.

---

## 4. Repository Structure

```
fitcoach-ai-tests/
│
├── README.md
├── TEST_PLAN.md                    # This document
│
├── conftest.py                     # Shared pytest fixtures (base URL, auth headers, HTTP client)
├── pytest.ini                      # Pytest configuration, markers, output format
├── requirements.txt                # Python dependencies
│
├── ai_generated/                   # Layer 1 — Adversarial & edge-case queries
│   ├── generate_cases.py           # Script: calls LLM to generate new adversarial queries
│   ├── adversarial_queries.json    # Output: versioned query bank
│   └── README.md
│
├── rag_eval/                       # Layer 2 — RAG quality evaluation
│   ├── golden_dataset.json         # Fixed questions + human-verified answers + source chunks
│   ├── eval_runner.py              # RAGAS evaluation script
│   ├── thresholds.json             # Minimum acceptable scores per metric
│   └── README.md
│
├── router_accuracy/                # Layer 3 — Router agent classification
│   ├── labeled_queries.json        # Query → expected_agent ground truth labels
│   ├── test_router_accuracy.py     # Accuracy / confusion matrix tests
│   └── README.md
│
├── api/                            # Layer 4 — API integration tests
│   ├── test_upload.py              # PDF upload endpoint tests
│   ├── test_query.py               # Query endpoint tests (happy path + adversarial)
│   ├── test_router_agent.py        # Router agent API behavior
│   ├── test_training_agent.py      # Training agent endpoint
│   ├── test_rehab_agent.py         # Rehabilitation agent endpoint
│   ├── test_nutrition_agent.py     # Nutrition agent endpoint
│   ├── test_auth.py                # Authentication and authorization
│   ├── test_error_handling.py      # Malformed requests, wrong content types, large files
│   └── README.md
│
├── e2e/                            # Layer 5 — Playwright UI tests
│   ├── playwright.config.ts
│   ├── tests/
│   │   ├── upload_flow.spec.ts     # PDF upload user flow
│   │   ├── query_flow.spec.ts      # End-to-end query → answer flow
│   │   ├── error_states.spec.ts    # Wrong file type, server error display
│   │   └── responsive.spec.ts      # Layout at different viewport sizes
│   └── README.md
│
├── scripts/
│   ├── run_tests.sh                # Master execution script (runs on Mac Mini)
│   ├── generate_report.sh          # Converts pytest JSON output to HTML report
│   └── refresh_adversarial.sh      # Re-runs LLM generation for Layer 1 queries
│
└── reports/                        # Generated output — gitignored
    ├── latest.json
    └── latest.html
```

---

## 5. Five-Layer Defense Architecture

### Layer 1 — AI-Generated Adversarial Test Cases

**Purpose:** Counteract the happy-path bias inherent in developer-written and AI-assisted tests by systematically generating inputs designed to expose failure modes.

**How it works:**

A script (`generate_cases.py`) sends a structured prompt to Claude instructing it to act as an adversarial user and generate query categories that are known to stress multi-agent routing and RAG retrieval:

- Cross-domain queries where intent spans two agents (e.g. "I have a knee injury but want to build muscle — what should I do?")
- Ambiguous queries with no clear fitness domain
- Emotionally framed queries ("I'm in pain and desperate, what exercises can I do?")
- Mixed Chinese/English queries
- Extremely long queries with multiple embedded questions
- Queries that reference a specific book or author the system may not have indexed
- Queries that attempt to elicit information outside the fitness domain

The output is a versioned JSON file committed to the repository. These queries are used as input to Layer 3 (router tests) and Layer 4 (API query tests).

**Refresh cadence:** Re-generate after every major feature addition, or monthly at minimum.

**Pass/fail criteria for adversarial queries:**

| Criterion | Threshold |
|---|---|
| No 5xx server errors | 100% — any unhandled exception is an immediate failure |
| Routing accuracy on adversarial set | Measured and reported separately from the clean labeled set; target ≥ 80% (10 points below the clean-set target to account for intentional ambiguity) |
| No response contains context intended for a different concurrent request | 100% — data cross-contamination is always a blocker |

These criteria are evaluated in `test_query.py` (API layer) when adversarial queries are injected, and in `test_router_accuracy.py` as a separate adversarial accuracy metric distinct from the clean-set score.

**Key files:** `ai_generated/generate_cases.py`, `ai_generated/adversarial_queries.json`

---

### Layer 2 — RAG Quality Evaluation

**Purpose:** Measure whether the retrieval pipeline actually returns relevant context and whether the LLM answer is grounded in that context rather than hallucinated.

**Framework:** [RAGAS](https://github.com/explodinggradients/ragas)

**Metrics:**

| Metric | What it measures | Acceptable threshold |
|---|---|---|
| Answer Faithfulness | Is the answer fully supported by retrieved chunks? | ≥ 0.80 |
| Answer Relevance | Does the answer actually address the question? | ≥ 0.75 |
| Context Precision | Are retrieved chunks relevant (not noisy)? | ≥ 0.70 |
| Context Recall | Were all necessary chunks retrieved? | ≥ 0.65 |

**Threshold calibration process:** The values above are targets, not starting points. Before enforcing thresholds in CI, run `eval_runner.py` against the current system to establish a measured baseline. Set initial enforced thresholds at the baseline minus 5 percentage points per metric. Record the baseline scores and date in `rag_eval/thresholds.json` alongside the enforced values. Tighten thresholds toward the targets above as retrieval quality improves — aim to close the gap quarterly. Never set a threshold the current system cannot pass; a permanently red L2 will be ignored.

**Golden dataset structure (`golden_dataset.json`):**

```json
[
  {
    "question": "What is the recommended rep range for hypertrophy?",
    "ground_truth": "Research supports 6–12 repetitions per set for hypertrophic adaptation...",
    "source_book": "science_of_muscle_growth.pdf",
    "expected_agent": "training"
  }
]
```

The golden dataset uses a fixed set of PDF books that must not change version without a corresponding dataset review. Human experts verify ground truth answers at creation time.

**Evaluation run:** `eval_runner.py` sends each question through the full pipeline, collects the response and retrieved context, and computes RAGAS metrics. Results below threshold trigger a test failure.

**Key files:** `rag_eval/golden_dataset.json`, `rag_eval/eval_runner.py`, `rag_eval/thresholds.json`

---

### Layer 3 — Router & Agent Logic Testing

**Purpose:** Treat the Router Agent as a multi-class classifier and measure its accuracy with a labeled ground-truth dataset. Separately, test each specialist agent's behavior when given mocked or edge-case inputs.

**Router accuracy test approach:**

A labeled JSON dataset (`labeled_queries.json`) contains queries with human-assigned ground truth routing labels:

```json
[
  { "query": "How many sets for bench press?", "expected_agent": "training" },
  { "query": "My lower back hurts after deadlifts", "expected_agent": "rehab" },
  { "query": "Is creatine safe to take daily?", "expected_agent": "nutrition" },
  { "query": "I have a torn ACL but want to stay fit", "expected_agent": "rehab" },
  { "query": "Best diet for powerlifting", "expected_agent": "nutrition" }
]
```

The test runner measures:

- Overall routing accuracy (target: ≥ 90%)
- Per-class recall (target: ≥ 85% per agent)
- Confusion matrix to identify which agent pairs are most commonly confused

**Agent degradation testing:** Each specialist agent is tested independently with mocked LLM responses (empty string, timeout simulation, malformed JSON) to verify graceful fallback behavior rather than an unhandled exception.

**Key files:** `router_accuracy/labeled_queries.json`, `router_accuracy/test_router_accuracy.py`

---

### Layer 4 — API Integration Testing

**Purpose:** Verify that every API endpoint behaves correctly under both normal and abnormal conditions. This is the primary layer for catching contract violations and pipeline-level bugs.

**Test case design approach:** Claude reads the application's FastAPI OpenAPI schema (`/openapi.json`) to understand all endpoints, their expected request shapes, response schemas, and status code contracts. From this, it generates a comprehensive test file set covering all endpoint behaviors. This is the primary way LLM involvement improves over manually written API tests — it catches parameter combinations and schema edge cases a developer might overlook.

**Coverage requirements per endpoint:**

| Case type | Example |
|---|---|
| Happy path | Valid PDF upload returns `201` with file ID |
| Missing required field | Query without `question` field returns `422` |
| Wrong content type | Sending `.docx` to the PDF upload endpoint |
| Auth failure | Request without valid token returns `401` |
| Oversized payload | PDF exceeding size limit returns `413` |
| Concurrent requests | 10 simultaneous queries via `asyncio.gather` in `pytest-asyncio`; assert each response contains only context retrieved for its own question (no session/context bleed between requests) |
| Agent-specific | Training agent returns response citing training-domain content |

**Framework:** `pytest` + `httpx` (async HTTP client matching FastAPI's async architecture) + `pytest-asyncio` (for concurrent request tests)

**Key files:** `api/test_upload.py`, `api/test_query.py`, `api/test_*.py`

---

### Layer 5 — Playwright E2E UI Testing

**Purpose:** Verify the React frontend from a real browser perspective — file upload interactions, query submission, response rendering, and error state display — catching frontend regressions that API tests cannot see.

**Core test scenarios:**

| Scenario | Description |
|---|---|
| PDF upload flow | Drag and drop a valid PDF → await parsing complete notification → confirm file appears in document list |
| Full query flow | Type a fitness question → submit → wait for streamed response → assert response is non-empty and contains domain-relevant content |
| Wrong file type | Upload a `.txt` file → assert clear error message appears, no crash |
| File size limit | Upload a PDF exceeding the configured limit → assert user-facing size error |
| Empty query | Submit empty input → assert validation prevents submission |
| Long query | Paste a 500-word query → assert system handles without layout breakage |
| Response citation | Assert that answers include source references (book title, page) |

**Configuration:**

```typescript
// playwright.config.ts
export default {
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    headless: true,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
};
```

**Known browser coverage gap:** Only Chromium is covered in the initial implementation. Firefox and WebKit (Safari) are not included. Add them to `projects` once the Chromium suite is stable.

**Key files:** `e2e/tests/*.spec.ts`, `e2e/playwright.config.ts`

---

## 6. Execution Environment

**Hardware:** Mac Mini M2 (192.168.0.109) running the full application stack.

**Why the Mac Mini:** Tests run against a live local stack — real FastAPI server, real PostgreSQL + pgvector, real Redis — rather than mocks. This is intentional. The bugs that slipped through earlier testing did so precisely because they only appear when all components interact under realistic conditions.

**Execution script (`scripts/run_tests.sh`):**

```bash
#!/bin/bash
set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
REPORT_DIR="reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "[$(date)] Starting FitCoach test run against $BASE_URL"
mkdir -p "$REPORT_DIR"

pytest api/ router_accuracy/ \
  --json-report \
  --json-report-file="$REPORT_DIR/api_${TIMESTAMP}.json" \
  --html="$REPORT_DIR/api_${TIMESTAMP}.html" \
  --tb=short \
  -v \
  --base-url="$BASE_URL"

python rag_eval/eval_runner.py \
  --output "$REPORT_DIR/rag_${TIMESTAMP}.json"

npx playwright test e2e/tests/ \
  --reporter=html \
  --output "$REPORT_DIR/e2e_${TIMESTAMP}/"

echo "[$(date)] Test run complete. Reports saved to $REPORT_DIR/"
```

**OpenClaw role in execution:** OpenClaw is used for AI-powered result interpretation, not for mechanical test execution. After a test run, the JSON report is passed to Claude via OpenClaw with the prompt: "Summarize which tests failed, identify patterns, and suggest likely root causes." This keeps execution deterministic while leveraging AI for the reasoning step where it adds genuine value.

**Test execution triggers:**

| Trigger | Layers run | Rationale |
|---|---|---|
| Pre-merge (every PR) | L4 API + L3 Router | Fast (<5 min), deterministic, catches contract regressions before they land |
| Nightly (scheduled, 02:00) | L4 + L3 + L5 E2E | E2E is slower (~15 min) and less suited to blocking individual PRs |
| Weekly (Monday 06:00) | All layers including L2 RAGAS | L2 is expensive (LLM calls per question); weekly cadence balances coverage against cost |
| Manual (`run_tests.sh`) | All layers | On-demand before releases or after infrastructure changes |

Nightly and weekly runs are scheduled via cron on the Mac Mini. Pre-merge runs require the Mac Mini to be reachable from the git host (configure a webhook or use a self-hosted runner). If the Mac Mini is unreachable, pre-merge checks fall back to a reduced smoke test (L4 happy-path only) that can run in GitHub Actions against a mocked stack.

---

## 7. AI Involvement Model

The following table defines precisely where AI is and is not involved in the testing workflow, to prevent over-reliance on non-deterministic components in the execution path.

| Activity | AI involved? | Role |
|---|---|---|
| Generating adversarial query bank | Yes | Claude generates queries from a structured prompt |
| Reading OpenAPI schema to design API tests | Yes | Claude generates initial test files from `/openapi.json` |
| Executing pytest test suite | No | Shell script, deterministic |
| Running RAGAS evaluation | No | RAGAS library, deterministic scoring |
| Running Playwright tests | No | Playwright test runner, deterministic |
| Interpreting failure reports | Yes | Claude reads JSON output, identifies patterns |
| Adding new test cases for regressions | Yes | Claude suggests cases based on the bug description |
| Deciding pass/fail | No | Threshold comparison in code, not LLM judgment |

---

## 8. Test Data Management

**PDF corpus strategy:** The test suite uses three distinct PDF sets (golden synthetic, real-world, and stress test), each mapped to specific layers. The query bank and book corpus must be co-designed — queries generated without reference to what books are loaded produce meaningless test cases. Full corpus design, storage locations, acquisition policy, and the Layer 1 query generation schema are documented in **[CORPUS_DESIGN.md](CORPUS_DESIGN.md)** at the repo root.

**Fixed golden books:** The golden corpus (Set A) consists of synthetic PDFs stored in `rag_eval/fixtures/` and tracked via **Git LFS**. To set up: install Git LFS (`brew install git-lfs`), run `git lfs install` once per machine. The `.gitattributes` entry is committed — no further configuration needed. Changing these files requires a full golden dataset review and RAGAS threshold recalibration.

**Golden dataset review:** When any of the following occur, the golden dataset must be reviewed and potentially updated: a new PDF book is added to the test corpus, a change is made to the chunking strategy, or the embedding model is updated.

**Adversarial query bank versioning:** The JSON output of the LLM generation script is committed to version control. Each refresh generates a new file with a timestamp suffix. This allows comparison of query banks across versions and prevents silent loss of adversarial coverage.

**No production data in tests:** Test PDFs and queries are purpose-created fixtures. No user-uploaded data from production is used in any test case.

---

## 9. Reporting

Each test run produces three report artifacts:

- `api_<timestamp>.html` — Human-readable pytest report for API and router tests
- `rag_<timestamp>.json` — RAGAS metric scores per question, with pass/fail per threshold
- `e2e_<timestamp>/` — Playwright HTML report with screenshots on failure

These files are gitignored (generated output, not source). For shared visibility, the run script sends a summary to Feishu via the bot integration when OpenClaw is available. If OpenClaw is unavailable, the run script falls back to writing a `reports/latest_summary.txt` to a shared network path (`/Volumes/shared/fitcoach-reports/`) accessible to the team. The HTML report at `reports/latest.html` is always available locally on the Mac Mini regardless of delivery status.

**Summary format (for OpenClaw/Feishu delivery):**

```
FitCoach Test Run — 2026-04-02 14:30
API tests:      47 passed / 2 failed
RAG quality:    Faithfulness 0.83 ✓ | Relevance 0.71 ✓ | Precision 0.68 ✓ | Recall 0.61 ✗
Router acc:     91.2% overall ✓
E2E tests:      12 passed / 0 failed

Failures:
- test_upload.py::test_pdf_size_limit — AssertionError: expected 413, got 500
- rag_eval: Context Recall below threshold (0.61 < 0.65) on 3 questions
```

---

## 10. Expansion Roadmap

Tests are added incrementally. The implementation order below does not follow the layer numbering (L1–L5) — it follows value and feasibility. L4 (API) is implemented first because it is deterministic, fast, and requires no external tooling beyond pytest. L1 (adversarial generation) is implemented last because it depends on the query endpoints being stable enough to run the generated cases against. The following sequence is recommended:

**Phase 1 — Foundation**  
Set up repository structure. Write `conftest.py` and `pytest.ini`. Confirm Mac Mini can run the test script against the live local stack.

**Phase 2 — Layer 4 (API)**  
Use Claude to read the FastAPI OpenAPI schema and generate initial API test files. Human review and supplement. Achieve ≥ 80% endpoint coverage.

**Phase 3 — Layer 3 (Router accuracy)**  
Build `labeled_queries.json` with 100+ human-labeled examples. Run baseline accuracy measurement. Add adversarial cases from Layer 1.

**Phase 4 — Layer 2 (RAG quality)**  
Assemble golden dataset with 30–50 questions. Set initial thresholds based on current system performance. Tighten thresholds as retrieval quality improves.

**Phase 5 — Layer 5 (Playwright E2E)**  
Record initial test flows using Playwright's codegen. Add assertions. Integrate into the master run script.

**Phase 6 — Layer 1 (Adversarial refresh cycle)**  
Establish a monthly cadence for refreshing the adversarial query bank. Route new adversarial queries into Layer 3 and Layer 4.

**Ongoing**  
Every reported production bug becomes a new test case before the fix is merged. This ensures the test suite grows to reflect real failure modes encountered in production, not just theoretical ones anticipated during development.

---

*This document is maintained alongside the test repository. Update version and date with each structural change to the testing architecture.*