# Layer 1: AI-Generated Adversarial Test Cases

**Purpose:** Counteract the happy-path bias in developer-written and AI-assisted tests by systematically generating inputs designed to expose failure modes in routing, retrieval, and error handling.

---

## Core Component: `generate_cases.py`

The script acts as a "synthetic user" that understands the system's boundaries. It reads the current corpus metadata, constructs a structured adversarial prompt, and produces a versioned `adversarial_queries.json` bank used downstream by Layer 3 and Layer 4.

### Execution Flow

1. **Load Metadata** — Extract book titles and domain descriptions from `CORPUS_DESIGN.md` (Set B entries)
2. **Prompt Construction** — Inject corpus metadata into a structured adversarial prompt that constrains the LLM to generate across all required categories (see below)
3. **LLM Call** — Invoke Claude to generate a batch of JSON-formatted cases
4. **Validation** — Validate output against the schema using Pydantic; reject and retry any malformed entries
5. **Storage** — Append timestamp and save to `adversarial_queries.json`

---

## Query Categories

All seven categories are required. The generation prompt must explicitly request a minimum number of entries per category to prevent the LLM from defaulting to whichever categories it finds easiest.

| Category | Description | Example |
|---|---|---|
| `cross_domain` | Intent spans two or more agents | "I have a knee injury but want to build muscle — what should I do?" |
| `ambiguous` | Missing intent or vague terminology; no clear fitness domain | "What's the best approach for someone like me?" |
| `emotional` | High-stress or frustrated user framing | "I'm in constant pain and desperate — what exercises won't make it worse?" |
| `out_of_corpus` | Topic not covered by any loaded PDF; system must not hallucinate | "What does Dr. Smith say about glucose periodization?" |
| `author_reference` | References a specific book or author that may not be indexed | "What does Rippetoe say about squat depth for beginners?" (only valid if *Starting Strength* is in the corpus) |
| `mixed_language` | Mixed Chinese and English in the same query | "我想增肌, but my coach says protein intake matters — how much is enough?" |
| `long_multipart` | Extremely long queries with multiple embedded sub-questions | A 100–200 word paragraph asking about training frequency, nutrition timing, and injury prevention in one submission |

**Note on `author_reference`:** This category requires corpus awareness. If the referenced author's book is loaded, the query is `answerable`. If not, it is `unanswerable`. The generation prompt must check the corpus list before assigning answerability.

---

## Data Schema (`adversarial_queries.json`)

```json
[
  {
    "id": "adv-001",
    "query": "I have a torn ACL but want to squat heavy — what is the risk?",
    "category": "cross_domain",
    "answerability": "answerable",
    "expected_agents": ["rehab", "training"],
    "created_at": "2026-04-02T15:30:00Z",
    "notes": "Both agents are defensible; tests whether supervisor handles dual-dispatch or picks one"
  },
  {
    "id": "adv-002",
    "query": "What does Dr. Smith say about glucose periodization?",
    "category": "out_of_corpus",
    "answerability": "unanswerable",
    "expected_agents": [],
    "created_at": "2026-04-02T15:30:00Z",
    "notes": "No book by Dr. Smith in corpus; system should return a graceful fallback, not a fabricated answer"
  }
]
```

**Field definitions:**

| Field | Type | Description |
|---|---|---|
| `id` | string | Stable identifier in format `adv-NNN`; never reused across versions |
| `query` | string | The query text sent to the system |
| `category` | string | One of the seven categories above |
| `answerability` | string | `"answerable"` or `"unanswerable"` — determines which assertion Layer 4 applies |
| `expected_agents` | array | For `answerable` queries, which agent(s) are defensible routing targets; empty for `unanswerable` |
| `created_at` | ISO 8601 | Generation timestamp |
| `notes` | string | Rationale for the query; used during failure investigation |

The `answerability` field is the critical one: it converts open-ended output review into a deterministic assertion. Layer 4 asserts a non-empty, domain-relevant response for `answerable` queries and a graceful fallback (no hallucination) for `unanswerable` queries.

---

## Pass/Fail Criteria

These thresholds are enforced in `test_query.py` (Layer 4) and `test_router_accuracy.py` (Layer 3) when adversarial queries are injected.

| Criterion | Threshold | Enforced in |
|---|---|---|
| No 5xx server errors on any adversarial query | 100% — any unhandled exception is an immediate failure | `test_query.py` |
| Routing accuracy on adversarial set | ≥ 80% — measured and reported **separately** from the clean labeled set to avoid masking adversarial failures | `test_router_accuracy.py` |
| `unanswerable` queries return a graceful fallback, not a hallucinated answer | 100% | `test_query.py` |
| No response contains context retrieved for a different concurrent request | 100% — data cross-contamination is always a blocker | `test_query.py` |

The adversarial routing accuracy target (≥ 80%) is intentionally 10 points below the clean-set target (≥ 90%) to account for genuine ambiguity in cross-domain and mixed-language queries.

---

## Downstream Consumers

This layer's output feeds directly into two other layers:

- **Layer 3 (`router_accuracy/test_router_accuracy.py`)** — loads `adversarial_queries.json` and runs routing accuracy measurement on the adversarial set separately from the clean `labeled_queries.json` dataset. Results are reported as a distinct metric.
- **Layer 4 (`api/test_query.py`)** — iterates over all entries, sends each query to the `/query` endpoint, and applies `answerability`-conditional assertions: content checks for `answerable`, fallback checks for `unanswerable`.

Any schema change to `adversarial_queries.json` must be reflected in both consumers.

---

## Usage

### 1. Installation
Requires **Python 3.11+**. Ensure all testing dependencies are installed:
```bash
pip install -r ../requirements.txt
```

### 2. Configuration
Copy the environment template and fill in your LLM credentials (DeepSeek, GLM, Kimi, or any OpenAI-compatible provider):
```bash
cp ../.env.example ../.env
```
Edit `../.env` with your API key, base URL, and preferred model name.

### 3. Execution
To refresh the adversarial query bank:
```bash
# Run the generator (defaults to 30 queries)
python generate_cases.py --count 50 --output adversarial_queries.json
```

**Available flags:**

| Flag | Default | Description |
|---|---|---|
| `--count` | 30 | Total number of queries to generate; distributed across all seven categories (minimum: 7) |
| `--batch-size` | 10 | Number of queries requested per LLM call; tune down if hitting token limits |
| `--output` | `adversarial_queries.json` | Output file path |

The script will automatically load corpus context from `CORPUS_DESIGN.md` and print a category distribution report upon completion.

---

## Refresh Cadence

Re-generate the query bank when any of the following occur:

| Trigger | Reason |
|---|---|
| A new book is added to Set B | Corpus-aware queries become stale; `author_reference` and `out_of_corpus` labels may be wrong |
| RAG chunking strategy changes | Retrieval difficulty changes; existing queries may no longer stress the right boundaries |
| A new agent or routing logic is added | New routing paths require new cross-domain and ambiguous cases targeting the new agent |
| Any other major feature addition | New system behavior may introduce new failure modes not covered by the existing bank |
| Monthly scheduled refresh | Minimum cadence regardless of changes, to prevent gradual coverage drift |

Each refresh produces a new timestamped file. Do not overwrite previous versions — keep at least the last two versions to allow comparison of query bank coverage across releases.
