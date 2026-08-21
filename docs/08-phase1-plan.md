# Phase 1 — Implementation Plan

Phase 1 is done when a user can log in, create an investigation, add targets, run a scan
against **real** providers, and explore a live evidence-backed graph — with tests, lint,
types, and security checks green.

## Work breakdown

| # | Deliverable | Definition of done |
|---|---|---|
| 1 | **Core config & DB** | `pydantic-settings` with production validators; async SQLAlchemy engine; portable `GUID`/`JSONB` types so tests run on SQLite |
| 2 | **Domain models** | 16 tables from the ERD, dedup uniques in place, cascade rules verified by `test_models.py` |
| 3 | **Schemas** | strict Pydantic v2 (`extra="forbid"`) for every request/response in the API contract |
| 4 | **Security core** | `SafeAsyncClient` (validate→resolve→pin→redirect-check→size-cap); JWT+Argon2id; RBAC deps; audit; API rate limit. `test_ssrf.py` covers all 8 attack rows of the threat model |
| 5 | **Provider runtime** | ABC + registry (filesystem discovery) + runner (cache→ratelimit→semaphore→timeout→retry) + memory/redis backends |
| 6 | **Evidence engine** | canonical keys, normalizer, extractor, sha256-deduped raw evidence store |
| 7 | **Graph store & projection** | dedup-safe upsert, edge `dedupe_key`, evidence-required invariant, clustering above threshold |
| 8 | **Correlation v1** | signal set + log-odds engine + bands + human-readable reasons; entity dedupe service |
| 9 | **Orchestrator & events** | job planning, budget enforcement, recursion loop-guard, inline+ARQ queues, event bus, WebSocket fan-out |
| 10 | **API** | every route in `05-api-contract.md`, OpenAPI clean, 401/403 correct on all of them |
| 11 | **Phase-1 providers** | `github`, `dns`, `whois`(RDAP), `crtsh`, `gravatar`, `website`, `username_enum`; adapters for `maigret`/`sherlock`/`holehe` that degrade to `unavailable`; `search` behind config |
| 12 | **Frontend MVP** | login → investigations → workspace: Cytoscape graph, sidebar, entity inspector, evidence panel, timeline slider, source manager, progress/stages, export |
| 13 | **Demo mode** | `POST /demo/seed` builds a full offline investigation; the UI is fully explorable with no network |
| 14 | **Export** | JSON, CSV, GraphML, HTML report, Markdown; PNG/SVG from the client |
| 15 | **Quality gates** | `ruff`, `mypy --strict` on `app/`, `pytest` (unit+integration+security), `bandit`, `pip-audit`, `tsc --noEmit`, `vitest`, CI workflow |
| 16 | **Ops** | `docker compose up -d` brings up frontend/backend/worker/postgres/redis with healthchecks and migrations on boot |

## Order of execution

```
1 → 2 → 3 → 4 ─┬─► 5 ──► 11
               ├─► 6 ──► 7 ──► 8
               └─► 9 ──► 10 ──► 12 ──► 13/14
                              15 runs continuously, 16 last
```

## Explicit exit criteria (§41)

```
pytest            all green, security tests included
ruff check        clean
mypy              clean on backend/app
bandit            no HIGH/MEDIUM
tsc --noEmit      clean
vitest            all green
docker compose up healthy on all five services
```

Anything not finished is written down in the README's *Status* section — no silent gaps.
