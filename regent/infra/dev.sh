#!/usr/bin/env bash
# Run Regent locally without Docker (PostgreSQL must be reachable at REGENT_DATABASE_URL).
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -e ".[providers,browser,dev]" >/dev/null
( cd apps/web && npm install --no-audit --no-fund >/dev/null )
REGENT_BACKGROUND_LOOP=1 uvicorn apps.api.main:app --port 8000 &
API=$!
( cd apps/web && REGENT_API_URL=http://localhost:8000 npx next dev -p 3000 ) &
WEB=$!
trap 'kill $API $WEB' EXIT
echo "API http://localhost:8000   Cockpit http://localhost:3000"
wait
