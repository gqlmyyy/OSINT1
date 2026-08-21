# GraphIntel OSINT — API Contract

Base: `/api/v1` · OpenAPI at `/api/docs` · all bodies/responses are strict Pydantic v2
models (`extra="forbid"`). Auth: `Authorization: Bearer <JWT>` on everything except
`/health` and `/auth/*`.

## Auth

| Method | Path | Role | Body → Response |
|---|---|---|---|
| POST | `/auth/register` | public¹ | `{email, username, password}` → `UserOut` |
| POST | `/auth/login` | public | `{username, password}` → `{access_token, refresh_token, expires_in}` |
| POST | `/auth/refresh` | public | `{refresh_token}` → token pair |
| GET | `/auth/me` | any | → `UserOut` |

¹ open only while no user exists (bootstrap) or when `ALLOW_REGISTRATION=true`.

## Investigations

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/investigations` | analyst | `{name, description?, tags?, config?}` → `InvestigationOut` |
| GET | `/investigations` | viewer | paginated `?limit&offset&q&status` |
| GET | `/investigations/{id}` | viewer | includes counts + progress |
| PATCH | `/investigations/{id}` | analyst | name/description/tags/notes |
| DELETE | `/investigations/{id}` | admin | cascade |
| POST | `/investigations/{id}/targets` | analyst | `{targets:[{type?, value}]}` — type auto-detected when omitted |
| POST | `/investigations/{id}/scan` | analyst | `{providers?[], max_depth?, recursive?}` → `ScanOut{job_ids, planned}` |
| POST | `/investigations/{id}/cancel` | analyst | stops queued jobs |
| GET | `/investigations/{id}/progress` | viewer | per-provider % (§12) |
| GET | `/investigations/{id}/entities` | viewer | `?type&min_confidence&q&limit&offset` |
| GET | `/investigations/{id}/relationships` | viewer | `?type&min_confidence` |
| GET | `/investigations/{id}/graph` | viewer | `?min_confidence&types&since&until&expand&limit` |
| GET | `/investigations/{id}/timeline` | viewer | `?entity_id` |
| GET | `/investigations/{id}/observations` | viewer | `?provider&entity_id` |
| GET | `/investigations/{id}/matches` | viewer | identity candidates + reasons |
| GET | `/investigations/{id}/search` | viewer | `?q` full-text over entities/identifiers/observations |
| POST | `/investigations/{id}/export` | viewer | `{format, scope, entity_ids?}` → file stream |
| POST | `/investigations/{id}/notes` | analyst | `{body, entity_id?}` |

## Entities & graph analytics

| Method | Path | Notes |
|---|---|---|
| GET | `/entities/{id}` | full inspector payload: attributes, sources, first/last seen, evidence, raw |
| GET | `/entities/{id}/relationships` | in + out edges with evidence |
| GET | `/entities/{id}/observations` | every observation, newest first |
| GET | `/entities/{id}/timeline` | §16 |
| POST | `/entities/{id}/expand` | queue recursive discovery from this node (depth-bounded) |
| GET | `/relationships/{id}` | “why does this edge exist?” payload (§39) |
| POST | `/investigations/{id}/analytics/shortest-path` | `{source_id, target_id}` → node/edge path |
| POST | `/investigations/{id}/analytics/common-neighbors` | `{a_id, b_id}` |
| GET | `/investigations/{id}/analytics/centrality` | `?metric=degree\|betweenness&limit` |
| GET | `/investigations/{id}/analytics/components` | connected components |
| GET | `/investigations/{id}/analytics/clusters` | label-propagation clusters |
| GET | `/investigations/{id}/analytics/duplicates` | suspected duplicate identities |

## Sources (§11)

| Method | Path | Role |
|---|---|---|
| GET | `/sources` | viewer — registry + live health + rate-limit config |
| PATCH | `/sources/{name}` | admin — `{enabled?, config?, rate_limit?}` |
| POST | `/sources/{name}/health` | analyst — re-run `health_check()` |

## AI (optional, §28–29)

| Method | Path | Notes |
|---|---|---|
| GET | `/ai/status` | provider, model, enabled |
| POST | `/investigations/{id}/ai/summarize` | grounded summary; every claim carries `evidence_ids` |
| POST | `/investigations/{id}/ai/explain-match` | `{candidate_id}` → explanation bound to stored reasons |

Returns `503 {"detail":"ai_disabled"}` when `AI_ENABLED=false`. The AI layer can only cite
`evidence_ids` that exist; unresolvable citations are stripped and the claim is dropped.

## Demo (§36)

`POST /demo/seed` → creates a fully populated offline investigation and returns its id.

## WebSocket

`GET /api/v1/ws/investigations/{id}?token=<jwt>` — server→client events:

```jsonc
{"event":"job_started",       "provider":"github", "job_id":"…"}
{"event":"provider_progress", "provider":"github", "done":8, "total":10}
{"event":"entity_discovered", "entity_id":"ent_123","type":"social_account","node":{…}}
{"event":"relationship_discovered","relationship_id":"rel_9","edge":{…}}
{"event":"match_found",       "candidate_id":"…","score":0.86,"band":"strong"}
{"event":"stage_changed",     "stage":"correlation"}      // §37 discovery→…→analysis
{"event":"investigation_completed","stats":{…}}
{"event":"error",             "provider":"sherlock","message":"timeout"}
```

## Conventions

* Errors: RFC-7807-ish `{"detail": str | {code, message, fields}}`; 401/403/404/409/422/429.
* Pagination: `?limit` (≤200, default 50) `&offset`; responses `{items, total, limit, offset}`.
* Idempotency: re-POSTing the same target is a no-op returning the existing row (409-free).
* Rate limiting: per-user token bucket, `429` + `Retry-After`.
* All timestamps are UTC RFC-3339. All ids are UUIDv4 strings.
