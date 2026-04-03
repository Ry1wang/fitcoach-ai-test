# PDF Corpus Design

**Version:** 1.0  
**Date:** 2026-04-02  
**Status:** Active  

---

## Purpose

This document defines the strategy for designing the PDF book corpus used across all five test layers of FitCoach AI. It exists because the **query bank and the book corpus must be co-designed** — generating adversarial queries without first deciding what books are in the system produces one of two useless outcomes: queries the system cannot answer because no relevant book was uploaded, or queries so generic that any book would satisfy them. Neither produces meaningful signal about actual system behavior.

This is a cross-cutting design document. It applies to all layers, not just RAG evaluation.

---

## Three Corpus Sets

### Set A — Golden Corpus (L2 RAG Evaluation + L3 Router Accuracy)

**Purpose:** Provide a fixed, never-changing baseline for RAGAS evaluation and router accuracy measurement.

**Key requirement:** Known content with verifiable, human-written ground truth. Every fact in these PDFs must be something you can point at in the source and confirm is correct.

**Approach: Use synthetic PDFs, not real books.**

Synthetic PDFs authored specifically for testing give you full control over what facts exist, eliminate copyright concerns, and make ground truth unambiguous. Each synthetic book should be 20–40 pages, dense with specific factual claims (numbers, named protocols, named techniques), and strictly bounded to one domain.

| File | Domain | Content to include |
|---|---|---|
| `training_fundamentals_test.pdf` | Training | Rep ranges (hypertrophy: 6–12, strength: 1–5), periodization models (linear, undulating), named compound movements and cues |
| `rehabilitation_protocols_test.pdf` | Rehab | Common injuries (ACL, rotator cuff, lower back), recovery timelines, specific contraindicated movements per injury |
| `nutrition_basics_test.pdf` | Nutrition | Macronutrient ratios, pre/post-workout timing windows, supplement evidence levels (creatine: strong; BCAAs: weak for sufficient protein intake) |

**Storage:** `rag_eval/fixtures/` — tracked via Git LFS (see [Storage and Acquisition](#storage-and-acquisition)).

**Change policy:** These files must not be modified once the golden dataset is finalized. Any change requires a full golden dataset review and RAGAS threshold recalibration.

---

### Set B — Real-World Corpus (L1 Adversarial Cases + L3 Router Boundary Testing + L4 API)

**Purpose:** Test the system under realistic conditions — real PDF formatting, real-world prose, and genuine domain overlap.

Real fitness books contain inconsistent headers, tables, footnotes, embedded images, and multi-column layouts that synthetic PDFs cannot replicate. Crucially, real books that span multiple fitness domains generate the hardest routing boundary cases.

**Selection criteria:**

| Domain | Recommended titles | What they test |
|---|---|---|
| Training | *Starting Strength* (Rippetoe), *Science and Practice of Strength Training* (Zatsiorsky) | Dense prescriptions; Zatsiorsky tests nuanced retrieval on periodization theory |
| Rehab | *Becoming a Supple Leopard* (Starrett), *Rebuilding Milo* (Horschig) | Starrett tests image-heavy parsing; Horschig is text-dense and rehab-specific |
| Nutrition | A sports nutrition textbook (not a diet book) | Textbooks have specific, scorable factual claims; diet books produce noisy RAGAS relevance scores |
| **Cross-domain** | *Any powerlifting book covering both programming and nutrition* | **Most important.** Books that legitimately span domains generate queries where a human expert might reasonably disagree about which agent should handle them — these are the exact router failure cases you need |

**Why cross-domain books matter most:** A query from a single-domain book has an obvious correct routing label. A query from a cross-domain book does not — and that ambiguity is where the Router Agent is most likely to fail. You need at least two cross-domain books in Set B to generate a meaningful boundary test dataset for L3.

**Storage:** On the Mac Mini at `/opt/fitcoach-tests/fixtures/real_world/`. Not committed to the repository (copyright). See [Storage and Acquisition](#storage-and-acquisition).

---

### Set C — Stress Test Corpus (L4 API Integration + L5 UI E2E)

**Purpose:** Test system robustness, not answer quality. These PDFs are designed to find failures in the upload pipeline, parser, and frontend error handling.

| File | What it tests |
|---|---|
| A PDF at or near the application's configured upload size limit | Upload pipeline timeout and memory behavior under large payloads |
| A scanned PDF with poor OCR quality | Whether the parser fails gracefully or silently produces garbage embeddings |
| A PDF in Chinese (or another CJK language) | Mixed-language query handling, if the system is expected to support it |
| A completely off-domain PDF (e.g., a software engineering or cooking book) | Whether the system handles "no relevant content found" with a clean fallback rather than a hallucinated answer |
| A PDF with a `.pdf` extension but corrupted or non-PDF content | Frontend and backend validation of file integrity |

**Storage:** On the Mac Mini at `/opt/fitcoach-tests/fixtures/stress/`. Synthetic or freely licensed files where possible; see [Storage and Acquisition](#storage-and-acquisition).

---

## Corpus-Aware Layer 1 Query Generation

Once the corpus is decided, the `generate_cases.py` prompt in Layer 1 must include explicit corpus context. Replace the generic instruction:

> "Generate adversarial queries for a fitness AI assistant"

with a corpus-aware version:

> "The system has been loaded with the following books: [list titles and a one-sentence description of each book's content]. Generate adversarial queries. For each query, label it as one of:
> - **answerable** — the corpus contains information to answer this, but routing or retrieval is difficult
> - **unanswerable** — the corpus does not contain the information; the system should respond that it cannot answer
>
> Include these categories: cross-domain queries, ambiguous queries, emotionally framed queries, mixed Chinese/English queries, queries referencing content not in the corpus, queries spanning two books."

This labeling change is significant: it converts open-ended output evaluation into deterministic assertions. For **answerable** queries you assert a non-empty, domain-relevant response. For **unanswerable** queries you assert a graceful fallback — not a hallucinated answer.

The `adversarial_queries.json` schema should be extended to include the label:

```json
[
  {
    "query": "I have a knee injury but want to build muscle — what should I do?",
    "category": "cross_domain",
    "answerability": "answerable",
    "expected_agents": ["rehab", "training"],
    "notes": "Both agents are defensible; tests whether supervisor handles dual-dispatch or picks one"
  },
  {
    "query": "What does Dr. Smith say about glucose periodization?",
    "category": "out_of_corpus",
    "answerability": "unanswerable",
    "expected_agents": [],
    "notes": "No book by Dr. Smith in corpus; system should not fabricate"
  }
]
```

---

## Storage and Acquisition

| Set | Location | Tracking method | Access |
|---|---|---|---|
| Set A (synthetic) | `rag_eval/fixtures/` in repo | Git LFS | All team members via `git lfs pull` |
| Set B (real-world) | `fixtures/real_world/` in repo | Git LFS | All team members via `git lfs pull` |
| Set C (stress) | `fixtures/stress/` in repo | Git LFS | All team members via `git lfs pull` |

**Git LFS setup (required once per machine):**

```bash
brew install git-lfs
git lfs install
# Already configured in .gitattributes — no further setup needed
```

**Copyright notice:** Set B files are commercially published books. They must not be committed to the repository, distributed as fixtures, or shared over any network path accessible outside your team. Each developer is responsible for obtaining their own legally licensed copy of any Set B title used in testing.

---

## Practical Starting Point

For the first test run, the full corpus is not required. Start with:

1. Two synthetic PDFs: `training_fundamentals_test.pdf` and `nutrition_basics_test.pdf` (Set A)
2. One real cross-domain book you already own (Set B)
3. One off-domain stress PDF — a freely licensed non-fitness book works (Set C)

Four files are sufficient for all five layers to produce meaningful signal. Expand the corpus only after the pipeline is confirmed to handle these correctly. Adding more books before the pipeline is stable will make it harder to distinguish corpus gaps from pipeline bugs.

This starting configuration aligns with the test plan's Phase 1–2 scope. The full 30–50 question golden dataset (Phase 4) should be assembled only after the Set A synthetic PDFs are finalized.

---

## Layer-to-Corpus Mapping

| Layer | Corpus sets | Notes |
|---|---|---|
| L1 — Adversarial Cases | Set B | Real-world formatting and cross-domain overlap generate the most challenging queries |
| L2 — RAG Quality Evaluation | Set A | Synthetic PDFs with deterministic ground truth; RAGAS scores are only meaningful against known content |
| L3 — Router Accuracy | Set A + Set B | Set A for clean baseline; Set B cross-domain books for boundary classification tests |
| L4 — API Integration | Set B + Set C | Set B for core pipeline verification; Set C for error handling, concurrency, and parser robustness |
| L5 — UI E2E | Set B + Set C | Image-heavy Set B books test response rendering; Set C corrupted/oversized files test frontend error states |

---

*This document is maintained at the repo root. Update version and date whenever corpus sets, storage locations, or the query generation schema change.*
