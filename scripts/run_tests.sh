#!/bin/bash
# FitCoach AI — Master Test Execution Script
# Runs on Mac Mini M2 (192.168.0.109) against the live local stack.
#
# Usage:
#   ./scripts/run_tests.sh                    # Run all layers
#   ./scripts/run_tests.sh --layers 4,3       # Run specific layers only
#   BASE_URL=http://192.168.0.109/api/v1 ./scripts/run_tests.sh
#
# Exit codes: 0 = all passed, 1 = one or more failures

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost/api/v1}"
REPORT_DIR="reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LAYERS="${1:-all}"

echo "========================================================"
echo " FitCoach AI Test Run — $(date)"
echo " Base URL : $BASE_URL"
echo " Layers   : $LAYERS"
echo "========================================================"
mkdir -p "$REPORT_DIR"

OVERALL_EXIT=0

# ---------------------------------------------------------------------------
# Layer 4 — API Integration Tests (always run)
# ---------------------------------------------------------------------------
echo ""
echo "[Layer 4] API Integration Tests..."
pytest api/ \
  --base-url="$BASE_URL" \
  --json-report \
  --json-report-file="$REPORT_DIR/l4_${TIMESTAMP}.json" \
  --html="$REPORT_DIR/l4_${TIMESTAMP}.html" \
  --self-contained-html \
  --tb=short \
  -v \
  -m "layer4 and not requires_corpus and not slow" \
  || OVERALL_EXIT=1

# ---------------------------------------------------------------------------
# Layer 3 — Router Accuracy Tests (always run)
# ---------------------------------------------------------------------------
if [ -d "router_accuracy" ] && ls router_accuracy/test_*.py &>/dev/null 2>&1; then
  echo ""
  echo "[Layer 3] Router Accuracy Tests..."
  pytest router_accuracy/ \
    --base-url="$BASE_URL" \
    --json-report \
    --json-report-file="$REPORT_DIR/l3_${TIMESTAMP}.json" \
    --html="$REPORT_DIR/l3_${TIMESTAMP}.html" \
    --self-contained-html \
    --tb=short \
    -v \
    || OVERALL_EXIT=1
else
  echo "[Layer 3] Skipped — no test files in router_accuracy/ yet."
fi

# ---------------------------------------------------------------------------
# Layer 4 (corpus-dependent) — requires documents to be loaded
# ---------------------------------------------------------------------------
if [[ "$LAYERS" == "all" || "$LAYERS" == *"4"* ]]; then
  echo ""
  echo "[Layer 4] API Integration Tests (corpus-dependent)..."
  pytest api/ \
    --base-url="$BASE_URL" \
    --json-report \
    --json-report-file="$REPORT_DIR/l4_corpus_${TIMESTAMP}.json" \
    --html="$REPORT_DIR/l4_corpus_${TIMESTAMP}.html" \
    --self-contained-html \
    --tb=short \
    -v \
    -m "layer4 and requires_corpus and not slow" \
    || OVERALL_EXIT=1
fi

# ---------------------------------------------------------------------------
# Layer 5 — Playwright E2E (slower, nightly only)
# ---------------------------------------------------------------------------
if [[ "$LAYERS" == "all" ]] && [ -d "e2e/tests" ]; then
  echo ""
  echo "[Layer 5] Playwright E2E Tests..."
  npx playwright test e2e/tests/ \
    --reporter=html \
    --output "$REPORT_DIR/e2e_${TIMESTAMP}/" \
    || OVERALL_EXIT=1
fi

# ---------------------------------------------------------------------------
# Layer 2 — RAG Quality Evaluation (expensive, weekly only)
# ---------------------------------------------------------------------------
if [[ "$LAYERS" == "all" ]] && [ -f "rag_eval/eval_runner.py" ]; then
  echo ""
  echo "[Layer 2] RAG Quality Evaluation..."
  python rag_eval/eval_runner.py \
    --base-url="$BASE_URL" \
    --output "$REPORT_DIR/rag_${TIMESTAMP}.json" \
    || OVERALL_EXIT=1
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "========================================================"
echo " Test run complete — $(date)"
echo " Reports saved to: $REPORT_DIR/"
if [ "$OVERALL_EXIT" -eq 0 ]; then
  echo " Status: ALL PASSED"
else
  echo " Status: ONE OR MORE FAILURES (see reports above)"
fi
echo "========================================================"

# Copy latest reports for quick access
cp "$REPORT_DIR"/l4_${TIMESTAMP}.html "$REPORT_DIR/latest.html" 2>/dev/null || true

exit "$OVERALL_EXIT"
