#!/bin/bash
# 運行 api/ 目錄下的所有 Layer 4 API 測試
#
# 用法：
#   ./scripts/run_api_tests.sh                        # 運行全部（含需要語料庫的測試）
#   ./scripts/run_api_tests.sh --no-corpus            # 跳過需要語料庫的測試
#   BASE_URL=http://192.168.0.109/api/v1 ./scripts/run_api_tests.sh
#
# 退出碼：0 = 全部通過，1 = 有失敗

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost/api/v1}"
REPORT_DIR="reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SKIP_CORPUS=0

# 解析參數
for arg in "$@"; do
  case "$arg" in
    --no-corpus) SKIP_CORPUS=1 ;;
  esac
done

echo "========================================================"
echo " FitCoach API Tests — $(date)"
echo " Base URL : $BASE_URL"
if [ "$SKIP_CORPUS" -eq 1 ]; then
  echo " 模式     : 跳過需語料庫的測試（--no-corpus）"
else
  echo " 模式     : 全部測試"
fi
echo "========================================================"

mkdir -p "$REPORT_DIR"

# 選擇 marker 過濾條件
if [ "$SKIP_CORPUS" -eq 1 ]; then
  MARKER="layer4 and not requires_corpus and not slow"
else
  MARKER="layer4 and not slow"
fi

python3 -m pytest api/ \
  --base-url="$BASE_URL" \
  -m "$MARKER" \
  --json-report \
  --json-report-file="$REPORT_DIR/api_${TIMESTAMP}.json" \
  --html="$REPORT_DIR/api_${TIMESTAMP}.html" \
  --self-contained-html \
  --tb=short \
  -v

EXIT_CODE=$?

# 複製最新報告
cp "$REPORT_DIR/api_${TIMESTAMP}.html" "$REPORT_DIR/api_latest.html" 2>/dev/null || true

echo ""
echo "報告已儲存至 $REPORT_DIR/api_${TIMESTAMP}.html"
exit $EXIT_CODE
