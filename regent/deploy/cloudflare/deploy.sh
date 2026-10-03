#!/usr/bin/env bash
# Put Regent on Cloudflare and print the link to open it (from any device).
#
#   ./deploy.sh
#
# Needs Docker (running) and Node 20+. Everything else it asks for as it goes:
#   - Cloudflare: signs you in with `wrangler login` in your browser, unless
#     CLOUDFLARE_API_TOKEN (Workers Scripts, Containers, Workers R2 Storage: Edit) is set;
#   - Claude: runs `claude setup-token` and asks you to paste what it prints, unless
#     CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY is set. Kept in ~/.regent-cloud for next time.
# Optional: CLOUDFLARE_ACCOUNT_ID (which account, if you have several), REGENT_ACCESS_KEY (reuse a
# key), BUILD_CA (a CA to trust while building behind a TLS-inspecting proxy).
# Running it again updates Regent in place; your data stays (it lives in R2, not in the container).
set -euo pipefail
cd "$(dirname "$0")"
export WRANGLER_SEND_METRICS=false
STATE="${REGENT_CLOUD_STATE:-$HOME/.regent-cloud}"
mkdir -p "$STATE" && chmod 700 "$STATE"

docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker (Docker Desktop), then run this again."; exit 1; }
[ -d node_modules ] || npm install --no-audit --no-fund --silent
W="npx --no-install wrangler"

# Cloudflare: a token if you set one, otherwise your own sign-in
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] && ! $W whoami --json >/dev/null 2>&1; then
  echo "Signing you in to Cloudflare (a browser window opens)…"
  $W login
fi
if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  CLOUDFLARE_ACCOUNT_ID="$($W whoami --json 2>/dev/null | python3 -c '
import json, sys
accts = (json.load(sys.stdin) or {}).get("accounts") or []
if len(accts) > 1:
    print("Using Cloudflare account: %s (set CLOUDFLARE_ACCOUNT_ID to pick another)" % accts[0].get("name"), file=sys.stderr)
print(accts[0]["id"] if accts else "")')"
fi
[ -n "$CLOUDFLARE_ACCOUNT_ID" ] || { echo "Could not tell which Cloudflare account to use; set CLOUDFLARE_ACCOUNT_ID."; exit 1; }
export CLOUDFLARE_ACCOUNT_ID

# Claude: what lets Regent think (your Claude plan via setup-token, or an API key)
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  if [ -s "$STATE/claude_token" ]; then
    CLAUDE_CODE_OAUTH_TOKEN="$(cat "$STATE/claude_token")"
  elif [ -t 0 ] && command -v claude >/dev/null; then
    echo "Regent needs to think with your Claude plan. Running: claude setup-token"
    claude setup-token
    read -rsp "Paste the token it printed (it is not shown): " CLAUDE_CODE_OAUTH_TOKEN; echo
    [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || { echo "No token given."; exit 1; }
    (umask 077; printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN" > "$STATE/claude_token")
  else
    echo "Set CLAUDE_CODE_OAUTH_TOKEN (run: claude setup-token) or ANTHROPIC_API_KEY, so Regent can think."; exit 1
  fi
  export CLAUDE_CODE_OAUTH_TOKEN
fi

if [ -z "${REGENT_ACCESS_KEY:-}" ]; then
  [ -s "$STATE/access_key" ] || (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$STATE/access_key")
  REGENT_ACCESS_KEY="$(cat "$STATE/access_key")"
fi

echo "1/5  Building Regent…"
TAG="regent:$(git -C ../.. rev-parse --short HEAD 2>/dev/null || date +%s)$(git -C ../.. diff --quiet 2>/dev/null || echo "-$(date +%s)")"
SECRET_ARGS=()
CA="${BUILD_CA:-}"
[ -z "$CA" ] && [ -n "${HTTPS_PROXY:-}" ] && [ -n "${SSL_CERT_FILE:-}" ] && CA="$SSL_CERT_FILE"
[ -n "$CA" ] && SECRET_ARGS=(--secret "id=build_ca,src=$CA" --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" --network host)
docker build -q --platform linux/amd64 ${SECRET_ARGS[@]+"${SECRET_ARGS[@]}"} -t "$TAG" ../.. >/dev/null

echo "2/5  Uploading it to your Cloudflare account…"
PUSHED="$($W containers push "$TAG" 2>&1)" || { echo "$PUSHED"; exit 1; }
IMAGE="$(grep -oE 'registry\.cloudflare\.com/[^ "]+' <<<"$PUSHED" | tail -1)"
IMAGE="${IMAGE:-registry.cloudflare.com/$CLOUDFLARE_ACCOUNT_ID/$TAG}"

echo "3/5  Making a place for your data (R2)…"
$W r2 bucket list 2>/dev/null | grep -q "regent-backups" || $W r2 bucket create regent-backups >/dev/null

echo "4/5  Publishing…"
python3 - "$IMAGE" <<'EOF'
import json, re, sys
src = open("wrangler.jsonc").read()
cfg = json.loads(re.sub(r"^\s*//.*$", "", src, flags=re.M))
cfg["containers"][0]["image"] = sys.argv[1]
cfg["containers"][0].pop("image_build_context", None)
cfg["main"] = "src/index.ts"
json.dump(cfg, open(".wrangler.deploy.json", "w"), indent=1)
EOF
OUT="$($W deploy -c .wrangler.deploy.json 2>&1)" || { echo "$OUT"; exit 1; }
URL="$(grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' <<<"$OUT" | head -1)"
APPS_OUT="$($W deploy -c wrangler.apps.jsonc 2>&1)" || { echo "$APPS_OUT"; exit 1; }   # where built apps open
(umask 077; python3 - "$REGENT_ACCESS_KEY" > "$STATE/secrets.json") <<'EOF'
import json, os, sys
s = {"REGENT_ACCESS_KEY": sys.argv[1]}
for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
    if os.environ.get(k):
        s[k] = os.environ[k]
print(json.dumps(s))
EOF
$W secret bulk "$STATE/secrets.json" -c .wrangler.deploy.json >/dev/null
rm -f "$STATE/secrets.json"

echo "5/5  Starting Regent (the first start takes a minute or two)…"
if [ -n "$URL" ]; then
  for _ in $(seq 1 90); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/healthz")" = "200" ] && break
    sleep 5
  done
  echo
  echo "Regent is on Cloudflare. Open this link once on each device (it remembers you after that):"
  echo "  $URL/?key=$REGENT_ACCESS_KEY"
  echo "$URL" > "$STATE/url"
else
  echo "$OUT"
fi
