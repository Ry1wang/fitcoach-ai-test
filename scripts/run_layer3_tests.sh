#!/bin/bash
# FitCoach AI — Layer 3 Router Accuracy Tests
#
# 前置條件：
#   文件語料庫必須已上傳（先執行 scripts/layer1_pre.py）
#
# 用法：
#   ./scripts/run_layer3_tests.sh                  # 完整測試（105 條，需 10-20 分鐘）
#   ./scripts/run_layer3_tests.sh --smoke          # 僅快速 smoke（5 條，約 1 分鐘）
#   BASE_URL=http://192.168.0.109/api/v1 ./scripts/run_layer3_tests.sh
#
# 退出碼：0 = 全部通過，1 = 有失敗

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost/api/v1}"
REPORT_DIR="reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SMOKE=0

for arg in "$@"; do
  case "$arg" in
    --smoke) SMOKE=1 ;;
  esac
done

echo "========================================================"
echo " FitCoach Layer 3 — Router Accuracy Tests — $(date)"
echo " Base URL : $BASE_URL"
if [ "$SMOKE" -eq 1 ]; then
  echo " 模式     : Smoke（5 條核心查詢）"
else
  echo " 模式     : Full（105 條完整測試，含 confusion matrix）"
fi
echo "========================================================"

mkdir -p "$REPORT_DIR"

if [ "$SMOKE" -eq 1 ]; then
  MARKER="layer3 and not slow"
  REPORT_SUFFIX="l3_smoke_${TIMESTAMP}"
else
  MARKER="layer3"
  REPORT_SUFFIX="l3_full_${TIMESTAMP}"
fi

python3 -m pytest router_accuracy/ \
  --base-url="$BASE_URL" \
  -m "$MARKER" \
  --json-report \
  --json-report-file="$REPORT_DIR/${REPORT_SUFFIX}.json" \
  --html="$REPORT_DIR/${REPORT_SUFFIX}.html" \
  --self-contained-html \
  --tb=short \
  -v \
  -s

EXIT_CODE=$?

# 更新 latest 軟連結
if [ "$SMOKE" -eq 1 ]; then
  cp "$REPORT_DIR/${REPORT_SUFFIX}.html" "$REPORT_DIR/l3_smoke_latest.html" 2>/dev/null || true
else
  cp "$REPORT_DIR/${REPORT_SUFFIX}.html" "$REPORT_DIR/l3_latest.html" 2>/dev/null || true
fi

echo ""
echo "報告已儲存："
echo "  HTML : $REPORT_DIR/${REPORT_SUFFIX}.html"
echo "  JSON : $REPORT_DIR/${REPORT_SUFFIX}.json"
echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "狀態：全部通過"
else
  echo "狀態：有失敗（查看 HTML 報告了解詳情）"
fi

exit $EXIT_CODE
