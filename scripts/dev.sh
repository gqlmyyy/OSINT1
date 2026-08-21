#!/usr/bin/env bash
# Local development without Docker: SQLite + inline queue, so no Postgres or Redis needed.
set -euo pipefail

cd "$(dirname "$0")/.."

export ENV=development
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./graphintel.db}"
export SECRET_KEY="${SECRET_KEY:-dev-secret-key-that-is-long-enough-for-local-work}"
export REDIS_URL="${REDIS_URL:-}"
export PYTHONPATH="$PWD/backend"

echo "backend  → http://localhost:8000/api/docs"
echo "frontend → http://localhost:5173"
echo

( cd backend && python3 -m uvicorn app.main:app --reload --port 8000 ) &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null || true' EXIT

if [ -d frontend/node_modules ]; then
  ( cd frontend && npm run dev )
else
  echo "frontend dependencies not installed; run: cd frontend && npm install"
  wait $BACKEND
fi
