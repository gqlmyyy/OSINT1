# GraphIntel OSINT — Database ERD

PostgreSQL 16. Every domain row uses a UUID primary key (portable `GUID` type: native
`uuid` on PostgreSQL, `CHAR(36)` on SQLite so the test suite runs without a server).

```
┌──────────────┐        ┌──────────────────┐
│    users     │1      *│  investigations  │
│──────────────│───────►│──────────────────│
│ id (PK)      │        │ id (PK)          │
│ email  (U)   │        │ owner_id (FK)    │
│ username (U) │        │ name, status     │
│ password_hash│        │ config (JSONB)   │
│ role         │        │ created/updated  │
└──────────────┘        └───┬───┬───┬───┬──┘
                            │   │   │   │
        ┌───────────────────┘   │   │   └──────────────────────┐
        │                       │   │                          │
        ▼                       ▼   ▼                          ▼
┌───────────────┐   ┌──────────────────┐            ┌────────────────────┐
│    targets    │   │     entities     │            │       jobs         │
│───────────────│   │──────────────────│            │────────────────────│
│ id (PK)       │   │ id (PK)          │            │ id (PK)            │
│ investigation │   │ investigation_id │            │ investigation_id   │
│ type, value   │   │ type             │            │ provider           │
│ normalized(U) │   │ label            │            │ target_type/value  │
└───────────────┘   │ canonical_key ───┼─┐          │ depth, status      │
                    │ attributes JSONB │ │ UNIQUE   │ attempts, error    │
                    │ confidence       │ │(inv,key) │ stats JSONB        │
                    │ first/last_seen  │ │          └─────────┬──────────┘
                    └──┬────┬──────┬───┘ │                    │1
                       │1   │1     │1    │                    ▼*
          ┌────────────┘    │      └─────┼──────┐   ┌────────────────────┐
          ▼*                ▼*           │      │   │   provider_runs    │
┌──────────────────┐ ┌──────────────┐    │      │   │────────────────────│
│   identifiers    │ │ observations │    │      │   │ id, job_id (FK)    │
│──────────────────│ │──────────────│    │      │   │ provider, status   │
│ id (PK)          │ │ id (PK)      │    │      │   │ started/finished   │
│ entity_id (FK)   │ │ entity_id FK │    │      │   │ observation_count  │
│ kind, value      │ │ investigation│    │      │   │ cache_hit, error   │
│ normalized       │ │ provider     │    │      │   └────────────────────┘
│ UNIQUE(ent,k,nv) │ │ source       │    │      │
└──────────────────┘ │ kind, url    │    │      │   ┌────────────────────┐
                     │ observed_at  │    │      │   │      sources       │
                     │ confidence   │    │      │   │────────────────────│
                     │ assertion    │    │      │   │ name (PK)          │
                     │ data JSONB   │    │      │   │ type, enabled      │
                     └──────┬───────┘    │      │   │ requires_api_key   │
                            │1           │      │   │ rpm, concurrency   │
                            ▼*           │      │   │ reliability        │
                     ┌──────────────┐    │      │   │ coverage (JSONB)   │
                     │   evidence   │    │      │   │ config (JSONB)     │
                     │──────────────│    │      │   └────────────────────┘
                     │ id (PK)      │    │      │
                     │ observation  │    │      │   ┌────────────────────┐
                     │ content_type │    │      │   │    audit_logs      │
                     │ sha256 (idx) │    │      │   │────────────────────│
                     │ payload JSONB│    │      │   │ id, actor_id       │
                     │ captured_at  │    │      │   │ action, target     │
                     └──────────────┘    │      │   │ ip, meta, at       │
                                         │      │   └────────────────────┘
       ┌─────────────────────────────────┘      │
       │                                        │
       ▼                                        ▼
┌────────────────────────┐          ┌──────────────────────────┐
│     relationships      │          │   identity_candidates    │
│────────────────────────│          │──────────────────────────│
│ id (PK)                │          │ id (PK)                  │
│ investigation_id (FK)  │          │ investigation_id (FK)    │
│ source_entity_id (FK)──┼─► entities│ entity_a_id (FK) ───────┼─► entities
│ target_entity_id (FK)──┼─► entities│ entity_b_id (FK) ───────┼─► entities
│ type (enum)            │          │ score  (0..1)            │
│ confidence, assertion  │          │ band (possible|probable| │
│ provider, evidence JSON│          │       strong|confirmed)  │
│ created_at             │          │ reasons JSONB (+/- each) │
│ UNIQUE(inv, dedupe_key)│          │ UNIQUE(inv, a, b)        │
└────────────────────────┘          └──────────────────────────┘

┌──────────────┐   ┌──────────────┐   ┌────────────────────────┐
│     tags     │   │    notes     │   │  investigation_tags     │
│ id, name (U) │◄──┤ id, inv_id   │   │  (investigation_id,tag) │
└──────────────┘   │ entity_id?   │   └────────────────────────┘
                   │ body, author │
                   └──────────────┘
```

## Cardinality summary

| Parent | Child | Rule |
|---|---|---|
| `users` 1 — * `investigations` | owner; `ON DELETE RESTRICT` |
| `investigations` 1 — * `targets` / `entities` / `jobs` / `notes` | `ON DELETE CASCADE` |
| `entities` 1 — * `identifiers` / `observations` | `ON DELETE CASCADE` |
| `observations` 1 — * `evidence` | raw payload, never overwritten |
| `entities` * — * `entities` via `relationships` | directed, deduped by `dedupe_key` |
| `jobs` 1 — * `provider_runs` | one row per attempt |

## Integrity rules that carry product meaning

1. `UNIQUE (investigation_id, canonical_key)` on `entities` is the **deduplication
   guarantee** (§18). `canonical_key` is derived, never user-supplied:
   `github:user:example_user`, `domain:example.com`, `email:a@b.c`.
2. `UNIQUE (investigation_id, dedupe_key)` on `relationships` where
   `dedupe_key = sha256(source||type||target)` — one edge per semantic fact; repeat
   observations bump `confidence` and append evidence instead of inserting a duplicate.
3. `relationships.assertion` and `observations.assertion` are constrained to
   `observed | inferred | correlated | unverified`. **No code path promotes `inferred`
   to `observed`** (§2) — the only writer of `observed` is the evidence extractor acting
   on a provider's direct response.
4. `evidence.sha256` indexes the raw payload so identical captures are stored once.
5. `entities.search_vector` (`tsvector`, GIN) backs §15 search on PostgreSQL; SQLite
   falls back to `LIKE` over `identifiers.normalized`.

## Indexes

```sql
CREATE UNIQUE INDEX ux_entity_canonical  ON entities(investigation_id, canonical_key);
CREATE UNIQUE INDEX ux_rel_dedupe        ON relationships(investigation_id, dedupe_key);
CREATE INDEX        ix_obs_entity_time   ON observations(entity_id, observed_at DESC);
CREATE INDEX        ix_ident_norm        ON identifiers(normalized);
CREATE INDEX        ix_rel_source        ON relationships(source_entity_id);
CREATE INDEX        ix_rel_target        ON relationships(target_entity_id);
CREATE INDEX        ix_jobs_status       ON jobs(investigation_id, status);
CREATE INDEX        gin_entity_fts       ON entities USING GIN(search_vector);  -- PG only
```
