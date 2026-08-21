#!/usr/bin/env bash
# Every quality gate the project must pass before a phase is considered done.
# Run from the repository root: ./scripts/check.sh
set -uo pipefail

cd "$(dirname "$0")/.."
export ENV=test
export SECRET_KEY="${SECRET_KEY:-test-secret-key-that-is-long-enough-for-checks-1234}"

FAILED=()
run() {
  local name="$1"; shift
  printf '\n\033[1m▸ %s\033[0m\n' "$name"
  if "$@"; then
    printf '\033[32m  ✓ %s\033[0m\n' "$name"
  else
    printf '\033[31m  ✗ %s\033[0m\n' "$name"
    FAILED+=("$name")
  fi
}

run "ruff (lint)"          python3 -m ruff check .
run "mypy (types)"         bash -c 'cd backend && python3 -m mypy app'
run "pytest (backend + plugins)" python3 -m pytest -q
run "bandit (security)"    python3 -m bandit -q -r backend/app plugins -ll

if [ -d frontend/node_modules ]; then
  run "tsc (frontend types)" bash -c 'cd frontend && npx tsc --noEmit'
  run "eslint (frontend)"    bash -c 'cd frontend && npx eslint src --ext .ts,.tsx'
  run "vitest (frontend)"    bash -c 'cd frontend && npx vitest run'
else
  printf '\n\033[33m▸ frontend checks skipped (run: cd frontend && npm install)\033[0m\n'
fi

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\033[32mAll checks passed.\033[0m\n'
  exit 0
fi
printf '\033[31mFailed: %s\033[0m\n' "${FAILED[*]}"
exit 1
