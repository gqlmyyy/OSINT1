#!/usr/bin/env bash
# Entrypoint for both roles: `api` serves HTTP, `worker` drains the scan queue.
set -euo pipefail

ROLE="${1:-api}"

wait_for_postgres() {
  [[ "${DATABASE_URL:-}" == postgresql* ]] || return 0
  echo "waiting for postgres…"
  python - <<'PY'
import asyncio, os, sys, time
import asyncpg

url = os.environ["DATABASE_URL"].replace("+asyncpg", "")

async def main() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(url)
            await connection.close()
            return
        except Exception as exc:  # noqa: BLE001
            print(f"  postgres not ready ({type(exc).__name__}); retrying")
            await asyncio.sleep(2)
    sys.exit("postgres did not become available in time")

asyncio.run(main())
PY
}

run_migrations() {
  [[ "${DATABASE_URL:-}" == postgresql* ]] || return 0
  echo "applying migrations…"
  alembic upgrade head
}

case "$ROLE" in
  api)
    wait_for_postgres
    run_migrations
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
    ;;
  worker)
    wait_for_postgres
    # Only the API applies migrations, so the worker waits for the schema instead.
    exec arq app.jobs.worker.WorkerSettings
    ;;
  *)
    exec "$@"
    ;;
esac
