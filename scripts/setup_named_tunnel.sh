#!/bin/zsh
# One-time setup of a STABLE public hostname for the dashboard via a named Cloudflare tunnel.
# Needs: a free Cloudflare account with a domain (zone) on it. The login step opens your browser once.
#   ./scripts/setup_named_tunnel.sh intel.yourdomain.com
set -e
HOST="$1"
[[ -z "$HOST" ]] && { echo "usage: $0 <hostname under your Cloudflare domain, e.g. intel.yourdomain.com>"; exit 1; }
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
CF=/opt/homebrew/bin/cloudflared
PY="${PYTHON:-/opt/anaconda3/bin/python3}"
# Safety gate: exposing the service before named accounts exist means everyone shares one token and the
# append-only audit log attributes every action to the same identity, which defeats its purpose.
if ! "$PY" -c "import sys; sys.path.insert(0,'$PROJECT'); from market_intel import identity; sys.exit(0 if identity.any_accounts() else 1)" 2>/dev/null; then
  echo "refusing to expose: no named accounts exist yet."
  echo "  create them first, e.g.:  python3 -m market_intel account add <user> --role analyst"
  echo "  roles: viewer analyst pm risk quantops admin"
  exit 1
fi
[[ -x "$CF" ]] || { echo "cloudflared missing: brew install cloudflared"; exit 1; }
[[ -f "$HOME/.cloudflared/cert.pem" ]] || "$CF" tunnel login                 # browser: authorise the zone
"$CF" tunnel list 2>/dev/null | grep -q "market-intel" || "$CF" tunnel create market-intel
UUID=$("$CF" tunnel list --output json | /opt/anaconda3/bin/python3 -c "import sys,json; print([t['id'] for t in json.load(sys.stdin) if t['name']=='market-intel'][0])")
mkdir -p "$HOME/.cloudflared"
cat > "$HOME/.cloudflared/config.yml" <<EOF
tunnel: $UUID
credentials-file: $HOME/.cloudflared/$UUID.json
ingress:
  - hostname: $HOST
    service: http://127.0.0.1:8787
  - service: http_status:404
EOF
"$CF" tunnel route dns market-intel "$HOST" || true                            # no-op if the DNS record already exists
DEST="$HOME/Library/LaunchAgents/com.siyanchen.marketintel.tunnel.plist"
sed "s#__PROJECT__#$PROJECT#g" "$PROJECT/scripts/com.siyanchen.marketintel.tunnel.named.plist" > "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
mkdir -p "$PROJECT/data_cache/market_intel"
echo "https://$HOST" > "$PROJECT/data_cache/market_intel/public_url"
cat <<DONE

Named tunnel running: https://$HOST  (stable hostname; survives reboots)

  Each person signs in with their OWN token, so the audit log names them:
      python3 -m market_intel account add <user> --role analyst|pm|risk|quantops|admin
      python3 -m market_intel account list
      python3 -m market_intel audit          # verify the hash chain

  STRONGLY RECOMMENDED - put Cloudflare Access in front, so unauthenticated
  requests never reach this machine at all:
      Cloudflare dashboard > Zero Trust > Access > Applications > Add
        type      Self-hosted
        domain    $HOST
        policy    Allow -> Emails / Email domain / Identity provider group
      Then every visitor authenticates at Cloudflare's edge BEFORE the tunnel forwards
      anything, and this service's own token check becomes the second layer.
DONE
