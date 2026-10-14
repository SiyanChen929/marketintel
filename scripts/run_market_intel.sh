#!/bin/zsh
# Runs the real-time market-intelligence service (poller + dashboard at http://127.0.0.1:8787). Used by launchd.
cd "$(dirname "$0")/.."
mkdir -p outputs/logs
PY="${PYTHON:-/opt/anaconda3/bin/python3}"
exec "$PY" -m market_intel serve "$@" >> outputs/logs/market_intel.out 2>&1
