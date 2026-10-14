#!/bin/zsh
# Manual full-pipeline helper: data -> analysis -> portfolio -> charts -> deck -> dashboard.
# The automatic exchange-session job invokes ai_semi_intel.daily directly and logs
# to outputs/logs/aisemi_daily.out; this helper writes run_YYYY-MM-DD.log.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p outputs/logs
PY="${PYTHON:-/opt/anaconda3/bin/python3}"
LOG="outputs/logs/run_$(date +%Y-%m-%d).log"
echo "=== $(date) start ===" >> "$LOG"
"$PY" -m ai_semi_intel.pipeline "$@" >> "$LOG" 2>&1 && echo "=== $(date) ok ===" >> "$LOG" || { echo "=== $(date) FAILED ===" >> "$LOG"; exit 1; }
