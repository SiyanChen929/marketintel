#!/bin/zsh
# Installs the always-on market-intelligence service (poller + dashboard) and the menu-bar companion as launchd
# login agents; add --tunnel to also install the Cloudflare quick tunnel (internet access, needs `brew install cloudflared`).
# Re-run after changing the plists. Remove with:
#   launchctl unload ~/Library/LaunchAgents/com.siyanchen.marketintel.plist ~/Library/LaunchAgents/com.siyanchen.marketintel.menubar.plist
set -e
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$PROJECT/outputs/logs"
AGENTS=(com.siyanchen.marketintel com.siyanchen.marketintel.menubar)
[[ " $* " == *" --tunnel "* ]] && AGENTS+=(com.siyanchen.marketintel.tunnel)
for name in "${AGENTS[@]}"; do
  [[ "$name" == "com.siyanchen.marketintel.menubar" && " $* " == *" --no-menubar "* ]] && continue
  DEST="$HOME/Library/LaunchAgents/$name.plist"
  sed "s#__PROJECT__#$PROJECT#g" "$PROJECT/scripts/$name.plist" > "$DEST"
  launchctl unload "$DEST" 2>/dev/null || true
  launchctl load "$DEST"
  echo "Installed: $DEST"
done
echo "Dashboard: http://127.0.0.1:8787   Logs: $PROJECT/outputs/logs/market_intel.log   Restart: launchctl kickstart -k gui/$(id -u)/com.siyanchen.marketintel"
