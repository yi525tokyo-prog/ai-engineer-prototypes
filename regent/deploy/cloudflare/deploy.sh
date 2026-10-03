#!/usr/bin/env bash
# Put Regent on Cloudflare and print the link to open it (from any device).
#
# Needs: Docker, Node 20+, and in the environment
#   CLOUDFLARE_API_TOKEN   (Workers Scripts, Containers and Workers R2 Storage: Edit)
#   CLOUDFLARE_ACCOUNT_ID
#   CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY
# Optional:
#   REGENT_ACCESS_KEY      reuse an existing key (otherwise one is made once and kept in ~/.regent-cloud)
#   BUILD_CA               a CA file to trust while building behind a TLS-inspecting proxy
# Running it again updates Regent in place; your data stays (it lives in R2, not in the container).
set -euo pipefail
cd "$(dirname "$0")"
: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}" "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID}"
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Set CLAUDE_CODE_OAUTH_TOKEN (run: claude setup-token) or ANTHROPIC_API_KEY, so Regent can think."; exit 1
fi
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID WRANGLER_SEND_METRICS=false

STATE="${REGENT_CLOUD_STATE:-$HOME/.regent-cloud}"
mkdir -p "$STATE" && chmod 700 "$STATE"
if [ -z "${REGENT_ACCESS_KEY:-}" ]; then
  [ -s "$STATE/access_key" ] || python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$STATE/access_key"
  REGENT_ACCESS_KEY="$(cat "$STATE/access_key")"
fi
chmod 600 "$STATE/access_key" 2>/dev/null || true

[ -d node_modules ] || npm install --no-audit --no-fund --silent
W="npx --no-install wrangler"

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
python3 - "$REGENT_ACCESS_KEY" <<'EOF' > "$STATE/secrets.json"
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
