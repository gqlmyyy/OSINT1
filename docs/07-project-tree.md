# GraphIntel OSINT — Project Tree

```
graphintel/
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI app factory, middleware, lifespan
│   │   ├── api/
│   │   │   ├── deps.py             auth deps, RBAC, ownership, pagination
│   │   │   ├── errors.py           exception handlers
│   │   │   └── routes/             auth · investigations · entities · graph
│   │   │                           analytics · sources · export · ai · demo · ws
│   │   ├── core/
│   │   │   ├── config.py           pydantic-settings, prod-safety validators
│   │   │   ├── db.py               async engine, session, GUID/JSONB types
│   │   │   ├── logging.py          structured logs + secret redaction filter
│   │   │   └── enums.py            TargetType EntityType RelationshipType Assertion…
│   │   ├── models/                 SQLAlchemy: user investigation target entity
│   │   │                           identifier observation evidence relationship
│   │   │                           job provider_run source tag note candidate audit
│   │   ├── schemas/                Pydantic v2 request/response contracts
│   │   ├── services/               investigation · entity · search · export
│   │   │                           source_registry · report · ai
│   │   ├── providers/
│   │   │   ├── base.py             OSINTProvider ABC + ProviderContext
│   │   │   ├── types.py            Target Observation Capabilities Health RateLimit
│   │   │   ├── registry.py         filesystem discovery + enable/disable
│   │   │   ├── runner.py           cache→ratelimit→semaphore→timeout→retry
│   │   │   ├── ratelimit.py        token bucket (memory | redis)
│   │   │   └── cache.py            TTL cache (memory | redis)
│   │   ├── evidence/
│   │   │   ├── canonical.py        canonical_key derivation (dedup ground truth)
│   │   │   ├── normalizer.py       value/URL normalization
│   │   │   ├── extractor.py        Observation → entities + edges + identifiers
│   │   │   └── store.py            raw evidence persistence (sha256-deduped)
│   │   ├── correlation/
│   │   │   ├── signals.py          pluggable match signals + likelihood ratios
│   │   │   ├── engine.py           log-odds combination, bands, explanations
│   │   │   └── dedupe.py           entity deduplication service
│   │   ├── graph/
│   │   │   ├── store.py            entity/relationship upsert (dedup-safe)
│   │   │   ├── projection.py       API/UI graph payload + clustering
│   │   │   └── analytics.py        paths, centrality, components, clusters
│   │   ├── jobs/
│   │   │   ├── queue.py            ARQ | inline backends
│   │   │   ├── orchestrator.py     plan → run → extract → correlate → emit
│   │   │   ├── budget.py           max_depth / max_entities / max_jobs
│   │   │   ├── events.py           event bus (memory | redis pubsub)
│   │   │   └── worker.py           ARQ worker entrypoint
│   │   ├── security/
│   │   │   ├── ssrf.py             SafeAsyncClient: validate→resolve→pin→redirect
│   │   │   ├── auth.py             JWT (access/refresh) + Argon2id
│   │   │   ├── rbac.py             Role enum + permission matrix
│   │   │   ├── ratelimit.py        per-user/IP API throttle middleware
│   │   │   └── audit.py            audit log writer
│   │   └── demo/seed.py            offline synthetic investigation (§36)
│   ├── alembic/                    migrations (env.py renders GUID/JSONB correctly)
│   ├── tests/                      unit · integration · security · provider mocks
│   ├── pyproject.toml              deps, ruff, mypy, pytest config
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── main.tsx  App.tsx  router
│   │   ├── api/                    typed client + React Query hooks + WS client
│   │   ├── components/             ui primitives, layout, panels
│   │   ├── graph/                  Cytoscape canvas, styles, layouts, toolbar
│   │   ├── investigations/         list, workspace, targets, progress, stages
│   │   ├── entities/               inspector, entity list, filters
│   │   ├── evidence/               evidence panel, relationship “why?” view
│   │   ├── timeline/               timeline + slider
│   │   ├── providers/              source manager
│   │   ├── store/                  zustand slices (graph filters, selection, ws)
│   │   └── tests/                  vitest + testing-library
│   ├── package.json  vite.config.ts  tailwind.config.js  tsconfig.json
│   └── Dockerfile
│
├── plugins/                        drop-in providers (no core change needed)
│   ├── github/ dns/ whois/ crtsh/ gravatar/ website/ username_enum/ search/
│   └── maigret/ sherlock/ holehe/          optional subprocess adapters
│
├── docker/                         nginx.conf, entrypoints
├── docs/                           01…08 design docs
├── scripts/                        dev.sh, check.sh, seed_demo.py
├── ruff.toml  pytest.ini            one lint and test policy for backend + plugins
├── LICENSE                         MIT
├── .github/workflows/ci.yml        lint · types · tests · security scans
├── docker-compose.yml              frontend backend worker postgres redis
├── .env.example
└── README.md
```
