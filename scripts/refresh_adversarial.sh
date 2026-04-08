#!/bin/bash
# Layer 1 — Adversarial Query Bank Refresh
#
# Re-runs the LLM generation script to produce a fresh adversarial_queries.json.
# Run monthly, or after any of the following:
#   - A new PDF book is added to the corpus
#   - A major feature change to the Router Agent
#   - A production bug that exposed a new failure category
#
# Usage:
#   ./scripts/refresh_adversarial.sh                   # default: 70 queries
#   ./scripts/refresh_adversarial.sh --count 100       # custom count
#   ./scripts/refresh_adversarial.sh --dry-run         # print prompt, no LLM call
#
# Exit codes: 0 = success, 1 = generation failed or validation errors exceeded

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/ai_generated/generate_cases.py"
OUTPUT="$REPO_ROOT/ai_generated/adversarial_queries.json"
BACKUP_DIR="$REPO_ROOT/ai_generated/archive"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Parse arguments
COUNT=70
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --count) shift; COUNT="${1:-70}" ;;
    --count=*) COUNT="${arg#*=}" ;;
    --dry-run) DRY_RUN=1 ;;
  esac
done

echo "========================================================"
echo " FitCoach — Adversarial Query Bank Refresh"
echo " Timestamp : $TIMESTAMP"
echo " Target    : $COUNT queries"
echo " Output    : $OUTPUT"
if [ "$DRY_RUN" -eq 1 ]; then
  echo " Mode      : DRY RUN (no LLM call)"
fi
echo "========================================================"

# Activate virtualenv if present
if [ -f "$REPO_ROOT/venv/bin/activate" ]; then
  source "$REPO_ROOT/venv/bin/activate"
fi

cd "$REPO_ROOT"

if [ "$DRY_RUN" -eq 1 ]; then
  python "$SCRIPT" --count "$COUNT" --dry-run
  exit 0
fi

# Back up existing file before overwriting
if [ -f "$OUTPUT" ]; then
  mkdir -p "$BACKUP_DIR"
  BACKUP="$BACKUP_DIR/adversarial_queries_${TIMESTAMP}.json"
  cp "$OUTPUT" "$BACKUP"
  echo "[Backup] Previous query bank saved to $BACKUP"
fi

# Run generation
echo "[Generate] Calling LLM to produce $COUNT adversarial queries..."
python "$SCRIPT" --count "$COUNT"

# Validate output
if [ ! -f "$OUTPUT" ]; then
  echo "[ERROR] Output file not created: $OUTPUT" >&2
  exit 1
fi

COUNT_ACTUAL=$(python3 -c "import json; d=json.load(open('$OUTPUT')); print(len(d))")
echo "[Done] Generated $COUNT_ACTUAL queries → $OUTPUT"

# Print category distribution
echo ""
echo "[Distribution]"
python3 -c "
import json
from collections import Counter
with open('$OUTPUT') as f:
    data = json.load(f)
cats = Counter(q.get('category','unknown') for q in data)
for k, v in sorted(cats.items()):
    print(f'  {k:<22s} {v}')
"

echo ""
echo "Next steps:"
echo "  1. Review $OUTPUT for quality"
echo "  2. Commit: git add ai_generated/adversarial_queries.json && git commit -m 'refresh: adversarial query bank $TIMESTAMP'"
echo "  3. Run Layer 3 adversarial tests: pytest router_accuracy/ -m 'layer3' -k 'adversarial'"
echo "========================================================"
