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
├── maigret/provider.py     ─┐
├── sherlock/provider.py     ├─ optional subprocess adapters, argv-only, no shell
└── holehe/provider.py      ─┘
```

`ProviderRegistry.discover(path)` scans each folder for `provider.py`, imports it with
`importlib.util`, and reads `PROVIDERS: list[type[OSINTProvider]]`. A plugin that raises
on import is reported as `unavailable` with the traceback — it never takes down startup.

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
