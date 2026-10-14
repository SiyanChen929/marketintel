#!/bin/zsh
# Installs exchange-session preparation from 07:15 ET, once per session.
set -e
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.siyanchen.aisemi.daily.plist"
sed "s#__PROJECT__#$PROJECT#g" "$PROJECT/scripts/com.siyanchen.aisemi.daily.plist" > "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "Installed: $DEST (exchange sessions from 07:15 ET; one attempt/session). Check with: python -m ai_semi_intel.daily --check"
echo "Real-time event monitoring: ./scripts/install_market_intel.sh (market_intel package, see docs/MARKET_INTEL.md)"
