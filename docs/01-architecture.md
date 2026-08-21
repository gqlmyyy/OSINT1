# GraphIntel OSINT — Architecture

> Evidence-first, graph-centric, modular OSINT platform for **public** information only.

## 1. System context

```
                     ┌───────────────────────────────────────────┐
                     │              Analyst (browser)            │
                     └───────────────┬───────────────────────────┘
                                     │ HTTPS / WSS
                     ┌───────────────▼───────────────────────────┐
                     │  Frontend — React + TS + Vite             │
                     │  Cytoscape graph · Zustand · React Query  │
                     └───────────────┬───────────────────────────┘
                                     │ REST /api/v1  +  WS /api/v1/ws
                     ┌───────────────▼───────────────────────────┐
                     │  Backend — FastAPI (ASGI)                 │
                     │  ┌─────────────────────────────────────┐  │
                     │  │ API layer   auth · RBAC · validation │ │
                     │  ├─────────────────────────────────────┤  │
                     │  │ Services    investigation lifecycle  │ │
                     │  ├──────────┬──────────┬───────────────┤  │
                     │  │ Evidence  │ Correl-  │ Graph engine  │ │
                     │  │ engine    │ ation    │ + analytics   │ │
                     │  ├──────────┴──────────┴───────────────┤  │
                     │  │ Provider runtime                     │ │
                     │  │ registry · ratelimit · cache · SSRF  │ │
                     │  └─────────────────────────────────────┘  │
                     └────┬───────────────┬──────────────────┬───┘
                          │               │                  │
             ┌────────────▼───┐  ┌────────▼──────┐  ┌────────▼────────┐
             │ PostgreSQL 16  │  │ Redis 7       │  │ Worker (ARQ)    │
             │ entities,      │  │ queue · cache │  │ same codebase,  │
             │ evidence, jobs │  │ pubsub · rate │  │ runs providers  │
             └────────────────┘  └───────────────┘  └────────┬────────┘
                                                             │ egress via SSRF guard
                                              ┌──────────────▼──────────────┐
                                              │ plugins/  (drop-in dir)     │
                                              │ github dns whois crtsh …    │
                                              │ maigret sherlock holehe     │
                                              └─────────────────────────────┘
```

## 2. Runtime flow of one investigation

```
POST /investigations            create workspace
POST /investigations/{id}/targets   normalize + validate identifiers
POST /investigations/{id}/scan  ──► Orchestrator
                                     │
                                     ├─ plan jobs  (target × capable provider)
                                     │      bounded by max_depth / max_entities / max_jobs
                                     ▼
                                  Queue (ARQ on Redis  |  inline asyncio in dev/test)
                                     │
                       ┌─────────────┴──────────────┐
                       │  ProviderRunner            │  per provider:
                       │  cache → ratelimit → retry │  rpm, concurrency, timeout, backoff
                       │  → provider.search()       │
                       └─────────────┬──────────────┘
                                     ▼  list[Observation]  (raw evidence attached)
                                Normalizer            canonical values, URL cleanup
                                     ▼
                            Entity Extractor          observation → entities + edges
                                     ▼
                            Deduplication             canonical_key upsert
                                     ▼
                            Correlation engine        log-odds identity scoring
                                     ▼
                            Graph store (Postgres)
                                     ▼
                            Event bus  ──► WebSocket ──► live graph update
                                     │
                                     └─ derived identifiers re-enqueued (depth+1)
```

## 3. Layer responsibilities

| Layer | Owns | Must not |
|---|---|---|
| `api/` | HTTP surface, authn/authz, request validation, serialization | contain OSINT logic |
| `services/` | investigation lifecycle, search, export, source registry | talk HTTP to the outside world |
| `providers/` | provider contract, registry, rate limit, cache, runner | know about the DB schema |
| `plugins/` | concrete data sources | import `graph`, `models`, or `api` |
| `evidence/` | observation normalization, entity extraction, evidence store | infer identity |
| `correlation/` | identity resolution, scoring, explanation | mutate provider output |
| `graph/` | graph projection, analytics, clustering | perform network I/O |
| `jobs/` | queueing, orchestration, budget enforcement, events | implement provider logic |
| `security/` | SSRF guard, JWT, RBAC, audit, secrets | be bypassable from `plugins/` |

## 4. Key architectural decisions & trade-offs

| # | Decision | Why | Trade-off / alternative |
|---|---|---|---|
| A1 | Graph stored **in PostgreSQL** (`entities` + `relationships`), analytics computed in-process with NetworkX | one datastore, transactional dedup, trivial ops; investigations are ≤10⁴ nodes | a native graph DB (Neo4j/Memgraph) wins above ~10⁶ edges. Projection layer is isolated in `graph/` so a swap touches one module |
| A2 | **Log-odds** identity scoring, not additive points | additive points saturate past 1.0 and cannot express negative evidence; log-odds composes and stays explainable | needs calibrated per-signal likelihood ratios; they ship as config, not code |
| A3 | Plugins loaded from a **filesystem directory** by `importlib` | new provider = new folder, zero core edits (req. §27) | plugin code is *trusted code* running in-process. Documented in the threat model; hard isolation would need a subprocess/WASM sandbox |
| A4 | Queue abstraction with **ARQ** and **inline** backends | prod gets a real worker; tests and `demo` run with no Redis | two code paths to keep honest — covered by the same orchestrator tests |
| A5 | SSRF guard **pins the resolved IP** and re-validates every redirect hop | closes the DNS-rebinding TOCTOU window that plain allow/deny lists leave open | requires SNI override support (`httpx` request extensions); a plugin that builds its own client bypasses it, so `health_check` + review gate that |
| A6 | External CLI tools (maigret/sherlock/holehe) are **optional subprocess adapters** | licence-clean, no vendored code, degrade to `unavailable` when absent | output format drift breaks parsing; adapters pin `--json`-style contracts and are covered by fixture tests |
| A7 | Postgres **full-text search** before Elasticsearch | one less service; `tsvector` handles 10⁵ rows fine | fuzzy/multilingual ranking is weaker. `SearchService` is the seam |

## 5. Non-goals

Authentication bypass, CAPTCHA solving, credential testing, private-profile access, session
theft, and vulnerability exploitation are **out of scope by design**. The provider contract
has no place to put a credential for a third-party *user* account, and the SSRF guard makes
the backend unusable as an internal-network probe.
