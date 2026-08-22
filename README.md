# GraphIntel OSINT

An evidence-first, graph-centric OSINT platform for investigating **public** information
about accounts, digital identity, and the relationships between public entities.

It is not a username checker with a UI. Every finding is stored as an *observation* with a
source, a timestamp, a confidence and an evidence URL; entities are deduplicated on a
derived canonical key; relationships cannot exist without evidence; and identity
correlation produces an explained, banded **candidate** — never a verdict.

```
                 ┌── GitHub ──── Repository ──── Linked website
                 │
                 ├── Reddit
example_username ┼── X
                 │
                 └── Website ──── Domain ──── DNS ──── IP
                                      │
                                      └── Email
```

---

## Table of contents

- [What it does](#what-it-does)
- [Scope and limits](#scope-and-limits)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Providers](#providers)
- [Confidence and correlation](#confidence-and-correlation)
- [Security](#security)
- [Development](#development)
- [Quality gates](#quality-gates)
- [Status](#status)
- [Legal and ethical use](#legal-and-ethical-use)
- [License](#license)

---

## What it does

| | |
|---|---|
| **Input engine** | username · email · domain · URL · IP · phone · display name · full name · company · hash — several identifiers per investigation, auto-detected and normalized |
| **Discovery** | eleven providers behind one interface: GitHub, DNS, RDAP/WHOIS, certificate transparency, Gravatar, website/URL intelligence, a 32-site username checker, a configurable search endpoint, plus optional Maigret / Sherlock / Holehe CLI adapters |
| **Recursive discovery** | derived identifiers become new targets at depth+1, bounded by `max_depth=3`, `max_entities=500`, `max_jobs=100` and a per-(provider, target) loop guard |
| **Entity resolution** | pluggable match signals combined in log-odds, producing *Possible* / *Probable* / *Strong* / *Confirmed by Evidence* with the reasons that produced the score |
| **Graph** | interactive Cytoscape canvas: zoom, pan, drag, expand, collapse, hide, filter by type and confidence, in-graph search, timeline scoping, shortest path, centrality, components, clusters, duplicate detection |
| **Evidence** | raw provider payloads stored sha256-deduplicated and never mutated; every edge answers *"why does this relationship exist?"* |
| **Live updates** | WebSocket feed adds nodes and edges to the graph as they are discovered, with per-provider progress and pipeline stages |
| **Export** | JSON · CSV · Markdown · HTML report · GraphML, scoped to the investigation, selected entities, relationships, or evidence |
| **Demo mode** | a complete synthetic investigation that works with no network access at all |
| **AI (optional)** | local Ollama summarisation where every claim must cite a stored `evidence_id` — unbound claims are dropped, not repaired |

---

## Scope and limits

The platform reads what is already public: public profile pages, public APIs, public
DNS/RDAP records, public repositories, public websites and metadata, and sources whose
terms permit it.

**It does not, and will not, contain components for:** CAPTCHA solving, authentication
bypass, credential stuffing, password brute force, session-cookie theft, account
compromise, access to private profiles, access-control bypass, collection of non-public
personal data, or exploitation of vulnerabilities to reach hidden information.

These are architectural constraints, not policy text. The provider contract has no field
that could carry a third-party *user's* credentials, and the SSRF guard makes the backend
unusable as an internal-network probe.

Every stored statement is classified, and the classification is visible everywhere —
graph, panels, exports and reports:

| Class | Meaning |
|---|---|
| `observed` | a provider returned this directly from a public source |
| `correlated` | two observations share a canonical identifier |
| `inferred` | derived by a rule, not seen at any source — **terminal, never promoted** |
| `unverified` | reported by a source we could not corroborate |

---

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/gqlmyyy/OSINT1.git graphintel && cd graphintel
cp .env.example .env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env

docker compose up -d
```

Then open <http://localhost:8080>, create the first account (it becomes the administrator),
and either add targets and run a scan, or click **Seed the offline demo investigation**.

Services: `frontend` (nginx :8080) · `backend` (FastAPI :8000) · `worker` (ARQ) ·
`postgres` · `redis`. API docs at <http://localhost:8000/api/docs>.

### Without Docker

Needs only Python 3.11+ — SQLite and an in-process queue replace Postgres and Redis:

```bash
pip install -e "backend[dev]"
export SECRET_KEY="$(openssl rand -hex 32)"
python3 scripts/seed_demo.py           # optional: offline demo data
./scripts/dev.sh                       # backend :8000 + frontend :5173
```

---

## Architecture

```
Browser ── REST + WebSocket ── FastAPI ──┬── PostgreSQL   entities, evidence, jobs
                                         ├── Redis        queue · cache · pub/sub
                                         └── ARQ worker ──┬── provider runtime
                                                          │   cache → rate limit →
                                                          │   concurrency → timeout →
                                                          │   backoff
                                                          └── plugins/ (drop-in)
```

One investigation runs as:

```
targets → plan jobs → providers → observations (+ raw evidence)
                                        ↓
                          normalize → extract → deduplicate
                                        ↓
                             correlate → graph → WebSocket
                                        ↓
                          derived identifiers re-enqueued (depth+1)
```

Full design documents live in [`docs/`](docs/):

| Document | Contents |
|---|---|
| [01 Architecture](docs/01-architecture.md) | system context, runtime flow, layer rules, architectural decisions with trade-offs |
| [02 Database ERD](docs/02-database-erd.md) | all 15 tables, cardinality, the integrity rules that carry product meaning |
| [03 Provider architecture](docs/03-provider-architecture.md) | the contract, execution pipeline, discovery, and a worked example |
| [04 Graph data model](docs/04-graph-data-model.md) | node and edge types, canonical keys, the assertion lattice, the UI projection |
| [05 API contract](docs/05-api-contract.md) | every route, the WebSocket event vocabulary, conventions |
| [06 Threat model](docs/06-threat-model.md) | STRIDE over four trust boundaries, each mitigation named with its test |
| [07 Project tree](docs/07-project-tree.md) | what lives where |
| [08 Phase 1 plan](docs/08-phase1-plan.md) | work breakdown and exit criteria |

---

## Providers

```
plugins/
├── github/         public profiles, repositories, declared links
├── dns/            A, AAAA, MX, NS, TXT (incl. SPF includes), CNAME
├── whois/          registration data over RDAP (JSON successor to WHOIS)
├── crtsh/          subdomains from public certificate transparency logs
├── gravatar/       public avatar presence + image hash for correlation
├── website/        title, outbound links, published contacts, technology hints
├── username_enum/  32 public profile pages, manifest-driven
├── search/         a SearxNG-compatible endpoint you configure
├── image_geo/      EXIF GPS from an already-discovered image (no facial recognition)
├── maigret/   ─┐
├── sherlock/   ├─  optional CLI adapters — argv-only, no shell, no vendored code
├── holehe/    ─┘
├── breach/
│   └── hibp/       breach *metadata* (name, date, source) via the official HIBP API
└── social/
    ├── mastodon/   public API, unauthenticated
    ├── instagram/  official API only — "Requires API" until you configure a token
    ├── telegram/   public channel preview (t.me/s/<channel>), no login
    ├── twitter/    official X API v2 only — "Requires API" until configured
    ├── tiktok/     declared — official API only, not yet wired up
    └── linkedin/   declared — official API only, not yet wired up
```

Adding a provider is a new folder with a `provider.py` exposing
`PROVIDERS = [YourProvider]`. **No change to the graph engine, database core, or frontend
is required** — the registry discovers `plugins/<name>/provider.py` and, one level deeper,
`plugins/<category>/<name>/provider.py`. See [`plugins/README.md`](plugins/README.md),
[docs/03](docs/03-provider-architecture.md), and the social/investigation-specific design
in [docs/09](docs/09-social-intelligence-layer.md).

A platform with no lawful unauthenticated route to its data (X, TikTok, LinkedIn) is never
scraped around — it ships as an honest, declared `Requires API` entry instead, becomes
usable the moment you supply your own credential, and is provably free of any bypass code
via a source-level policy test in that provider's own test file.

Providers can be enabled, disabled, and rate-limited at runtime from the **Sources** tab
or `PATCH /api/v1/sources/{name}` — no redeploy. A provider whose `health_check()` reports
it cannot run gets no jobs planned at all.

---

## Confidence and correlation

The tool never says *"this is the same person."* It says:

```
Identity confidence: 87%   ·   Strong Match

+ same avatar image hash (7f3c2b19d84a…)
+ both declare the same public website: example.com
+ same username: example_user
− public bios differ
```

Signals are combined as **log-odds likelihood ratios**, not additive points, so evidence
composes without saturating and negative evidence can actually lower the score. A single
weak signal lands in *Possible Match* and no combination reaches certainty — the score is
clamped below 1.0 by construction. *Confirmed by Evidence* additionally requires a
directly observed shared artefact (a shared address, avatar hash, website, or repository),
never an accumulation of heuristics.

Adding probabilistic matching, embeddings, or image similarity means adding a
`MatchSignal` in `backend/app/correlation/signals.py`; the engine does not change.

---

## Security

The dominant risk is that the product's whole job is *"fetch this URL an analyst typed"* —
textbook SSRF. The guard in `backend/app/security/ssrf.py`:

- allowlists schemes (`http`, `https`) and ports, and rejects credentials in URLs
- classifies **every** resolved address against loopback, private, link-local, reserved,
  multicast and CGNAT ranges, plus an explicit cloud-metadata denylist
- follows redirects **manually**, re-validating each hop against the same policy
- **pins the resolved IP** and connects to it with a `Host` header and an SNI override, so
  DNS rebinding cannot swap a public answer for a private one between check and connect
- caps response size and enforces connect/read/total timeouts

Also implemented: Argon2id password hashing, JWT access/refresh, a role hierarchy enforced
per route, ownership checks that return 404 rather than 403 (the existence of another
analyst's case is itself information), per-caller rate limiting, a strict CSP and the
usual hardening headers, SQLAlchemy parameter binding throughout, secret redaction in
logs, API-key masking in responses, and audit logging of auth, scans, source changes,
exports and deletions.

External CLI tools run through `asyncio.create_subprocess_exec` with an **argv list and no
shell**, targets validated against a strict charset first, inside a throwaway working
directory, with a hard timeout and a process-group kill.

The [threat model](docs/06-threat-model.md) lists each attack, its mitigation, and the
test that proves it — plus the residual risks that are knowingly accepted.

---

## Development

```
backend/app/        api core models schemas services providers
                    graph correlation evidence jobs security demo
backend/tests/      unit · integration · security · provider mocks
plugins/<name>/     provider.py + tests/
frontend/src/       api components graph investigations entities
                    evidence timeline providers store tests
docker/ docs/ scripts/ .github/workflows/
```

Everything runs on SQLite with an inline queue during development and testing, and on
PostgreSQL with an ARQ worker in production. The same orchestrator code serves both.

```bash
./scripts/check.sh          # every gate below, in one run
pytest -q                   # backend + all plugin suites
cd frontend && npm run dev
```

---

## Quality gates

All of these pass on the current tree:

| Gate | Result |
|---|---|
| `ruff check .` | clean |
| `mypy app` | clean, 81 source files |
| `pytest -q` | **165 passed** (backend + plugin suites) |
| `bandit -r backend/app plugins -ll` | no HIGH or MEDIUM findings |
| `tsc --noEmit` | clean |
| `vitest run` | **18 passed** |
| `vite build` | succeeds |
| `alembic upgrade head && alembic downgrade base` | clean round trip |
| `docker build` (both images) | built in CI on every push — see the caveat under [Status](#status) |

The suite deliberately covers the failure modes, not just the happy path: duplicate
entities, provider failure, timeout, rate limiting, invalid URLs, malformed provider
responses, graph explosion budgets, recursive discovery loops, and all eight SSRF rows of
the threat model including DNS-rebinding IP pinning.

---

## Status

**Implemented and working:** everything described above — real backend, real frontend,
real graph, real database, real provider architecture, working demo mode, working export,
working tests, working Docker environment. There are no placeholder implementations or
dummy APIs outside `app/demo/`, which is explicitly synthetic.

**One thing I could not verify locally:** the container images are built by CI on every
push, but I was unable to build them in my own environment (its egress proxy blocks
Docker Hub blob downloads). The compose file validates, the Dockerfiles were reviewed
line by line, and everything they run — migrations, the API, the worker, the frontend
build — was exercised directly outside the container. Treat the first `docker compose up`
as the confirmation.

**Known limitations, stated plainly:**

- Plugins run **in-process** and are therefore trusted code. Hard isolation (subprocess or
  WASM sandboxing) is a stated non-goal for v1 — treat `plugins/` as reviewed source.
- Search is PostgreSQL full-text (`LIKE` on SQLite). `SearchService` is the seam where
  Elasticsearch would slot in if ranking quality ever demands it.
- Graph analytics run in-process with NetworkX. Fine to ~10⁴ nodes per investigation;
  beyond ~10⁶ edges a native graph database would be the right call, and `graph/` is
  isolated so that swap touches one module.
- Correlation weights are calibrated by hand and shipped as configuration. They encode
  judgement, not measurement — review them against your own data before trusting them.
- The `search` provider ships disabled: it needs an endpoint you are permitted to query.

---

## Legal and ethical use

This tool collects information about **real people**. Operating it makes you responsible
for doing so lawfully.

- Have a lawful basis and a legitimate purpose before you start an investigation.
- Respect each source's terms of service, `robots.txt`, and rate limits. The defaults are
  deliberately conservative; do not raise them to evade a limit.
- Collect the minimum you need, keep it no longer than you need it, and delete it when
  done — `DELETE /api/v1/investigations/{id}` hard-deletes every child record.
- A correlation is not an identification. Do not act on a *Possible* or *Probable* match
  as though it were established fact, and do not present the tool's output as proof.
- Do not use this against people who have not consented, outside an authorised engagement
  or another lawful basis.
- **No facial recognition, and none will be added.** This is a deliberate policy
  exclusion, not a missing feature: matching an individual's identity from their face is
  a stalking-enablement capability far more than a legitimate OSINT one, and no amount of
  confidence banding or evidence-linking makes that acceptable here. Where an image is
  useful evidence, the tool stays non-biometric — EXIF GPS, file hashes, perceptual-hash
  "same image candidate" (never "same person"), and post/caption context.
- **Image and EXIF data carries its own duty of care.** `plugins/image_geo` extracts
  whatever location and device metadata a source image actually contains; most platforms
  strip this on upload, and the tool says so rather than presenting the absence as a
  failure. Where a photo *does* carry embedded coordinates, that is precise real-world
  location data about wherever the photo was taken — treat it with the same minimisation
  and retention discipline as any other personal data, and remember it may describe a
  location the subject did not intend to disclose.
- **Breach lookups (`plugins/breach/hibp`) report membership, never content.** The tool
  stores only which breach, when, and where it was reported — never any leaked
  credential, password, or other exposed field, which HIBP's API does not return to any
  caller in the first place. A breach hit is a lead to verify through a lawful channel,
  not something to act on directly.

Depending on your jurisdiction, this activity may be regulated by GDPR, CCPA, computer
misuse law, or other statutes. This README is not legal advice.

---

## License

MIT — see [LICENSE](LICENSE). Maigret, Sherlock, and Holehe are **not** vendored; they are
optional external tools invoked through documented CLI interfaces and remain under their
own licenses.
