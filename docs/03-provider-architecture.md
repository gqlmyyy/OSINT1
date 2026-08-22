# GraphIntel OSINT — Provider Architecture

A provider is the only component allowed to touch the outside world. Adding one is a
**new folder in `plugins/`** — no change to the graph engine, database core, or frontend.

## 1. Contract

```python
class OSINTProvider(ABC):
    name: str                 # stable id, matches the plugin folder
    provider_type: ProviderType

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]: ...
    async def health_check(self) -> ProviderHealth: ...
    def capabilities(self) -> ProviderCapabilities: ...
```

`ProviderCapabilities` declares what the provider accepts and emits, so the orchestrator
can plan jobs without hardcoding provider names:

```yaml
accepts:        [username, email, domain]      # TargetType values
emits:          [social_account, domain, url]  # EntityType values
requires_api_key: false
rate_limit:  {rpm: 60, concurrency: 5, timeout_seconds: 15}
reliability: 0.9        # prior on provider correctness, feeds confidence
cost:        free
recursive:   true       # may its output be re-fed as new targets?
```

## 2. Execution pipeline

```
Target ─► capability match ─► cache lookup  (provider:kind:value, TTL)
                                   │hit → observations (cache_hit=true)
                                   │miss
                                   ▼
                            rate limiter (token bucket, rpm)
                                   ▼
                            concurrency semaphore
                                   ▼
                            timeout + retry(exponential backoff + jitter)
                                   ▼
                            provider.search(target, ctx)
                                   │
                                   │ ctx.http  ← the ONLY sanctioned egress:
                                   │   SSRF-guarded, IP-pinned, redirect-validated,
                                   │   size-capped, UA-identified client
                                   ▼
                            list[Observation]  (+ raw evidence)
                                   ▼
                            ProviderRun row  (status, count, duration, error)
```

Every stage failure is *recorded*, never swallowed: `ProviderRun.status ∈
{ok, empty, error, timeout, rate_limited, unavailable, skipped}`.

## 3. Registry & discovery

```
plugins/
├── github/provider.py      → PROVIDERS = [GitHubProvider]
├── dns/provider.py
├── whois/provider.py       (RDAP over HTTPS — no whois binary needed)
├── crtsh/provider.py       (certificate transparency)
├── gravatar/provider.py
├── website/provider.py     (URL intelligence: title, links, emails, socials)
├── username_enum/provider.py  (+ platforms.json manifest)
├── search/provider.py      (SearxNG-compatible JSON endpoint)
├── image_geo/provider.py   (EXIF GPS from a fetched image; see docs/06 §image_geo)
├── maigret/provider.py     ─┐
├── sherlock/provider.py     ├─ optional subprocess adapters, argv-only, no shell
├── holehe/provider.py      ─┘
├── breach/
│   └── hibp/provider.py    (breach *metadata* via the official HIBP API)
├── self_osint/
│   └── instagram_self/provider.py  (your *own* account, per-user OAuth token)
└── social/
    ├── mastodon/provider.py   (public API, unauthenticated)
    ├── instagram/provider.py  (official API only)
    ├── telegram/provider.py   (public channel preview, unauthenticated)
    ├── twitter/provider.py    (official X API v2 only)
    ├── tiktok/provider.py     (declared; official API only, not yet configured)
    └── linkedin/provider.py   (declared; official API only, not yet configured)
```

`ProviderRegistry.discover(path)` scans each top-level folder for a `provider.py`, and —
if none is found there — one level deeper (`plugins/<category>/<name>/provider.py`). It
imports each with `importlib.util` and reads `PROVIDERS: list[type[OSINTProvider]]`. The
nesting is a generic mechanism, not specific to `social/`: `breach/`, or any future
category folder, works the same way with no registry change. A plugin that raises on
import is reported as `unavailable` with the traceback — it never takes down startup.

### 3.1 The "honest degrade" shape

Several providers above (`instagram`, `twitter`, `tiktok`, `linkedin`) share one pattern,
used whenever a platform's data is not reachable without either an official API credential
or defeating an access control this project will not defeat:

* `capabilities().requires_api_key = True`,
* `health_check()` returns `ProviderHealth.unavailable(name, <actionable instructions>)`
  when no credential is configured — never a generic failure, always what to do,
* `search()` returns `[]` immediately when unconfigured — no network call is made,
* a source-level (not docstring-level) AST policy test asserts the file contains no
  session/cookie/CAPTCHA/bot-token/browser-automation machinery in executable code, so a
  future edit that adds a bypass fails the suite instead of silently shipping.

### 3.2 Per-run credentials (`instagram_self`)

Most providers get their configuration from the `sources` table, which is deployment-wide.
The self-audit provider cannot: its credential belongs to *one user*, and two users must
never share one. `ProviderRunner.run()` therefore accepts an optional `config` overlay,
applied to that call only, which `app/selfosint/service.py` populates with the requesting
user's own decrypted token.

Two consequences worth stating, because getting either wrong would be a data-isolation bug:

* the runner for this path is constructed with **`use_cache=False`** — the provider cache
  is keyed by provider + target, so two users auditing accounts with the same handle would
  otherwise collide on one cache entry;
* the provider's registry-level `health_check()` is permanently `unavailable`, because
  there is no global credential to check. Availability is per user and is reported by the
  Self-OSINT endpoints instead.

`tiktok` and `linkedin` take this to its logical end: since neither platform has *any*
unauthenticated path, their providers are pure declarations — `search()` never makes a
request at all. They exist only so the platform shows up, correctly, as `Requires API` in
the source list and platform dashboard instead of being silently missing.

Runtime enable/disable and per-provider config live in the `sources` table and are edited
through `/api/v1/sources` (§11), so an operator can turn a provider off without a redeploy.

## 4. External-tool adapters

`maigret`, `sherlock`, and `holehe` are **not vendored**. Each adapter:

* locates the binary with `shutil.which()`; absent ⇒ `health_check() → unavailable`,
  and the orchestrator plans no jobs for it,
* executes with `asyncio.create_subprocess_exec` — **argv list, `shell=False`**,
* validates the target against a strict charset before it ever reaches argv,
* runs in a temp working directory, with a hard timeout and process-group kill,
* parses the tool's JSON report into `Observation`s, attaching the raw report as evidence.

This keeps licences clean, upgrades independent, and command injection impossible.

## 5. Confidence assignment

A provider never invents a number. Observation confidence is
`clamp(provider.reliability × signal_strength)` where `signal_strength` comes from the
match kind the provider itself reports (`exact_id` 1.0, `claimed_link` 0.9,
`pattern_match` 0.6, `weak_heuristic` 0.35). Everything a provider cannot verify is
emitted with `assertion=unverified`.

## 6. Writing a new provider

```python
# plugins/example/provider.py
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (Observation, ProviderCapabilities, ProviderHealth,
                                 ProviderRateLimit, ProviderType, Target)

class ExampleProvider(OSINTProvider):
    name = "example"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(accepts=["username"], emits=["social_account"],
                                    rate_limit=ProviderRateLimit(rpm=30, concurrency=3))

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name)

    async def search(self, target, ctx):
        r = await ctx.http.get(f"https://example.com/u/{target.value}")
        if r.status_code != 200:
            return []
        return [self.observation(kind="social_account", url=str(r.url),
                                 data={"username": target.value}, match="exact_id")]

PROVIDERS = [ExampleProvider]
```

Add `plugins/example/tests/test_provider.py` with a mocked transport and it is done.
