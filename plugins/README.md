# Provider plugins

Each folder here is a self-contained provider. Adding one requires **no change** to the
graph engine, the database core, or the frontend.

```
plugins/<name>/provider.py     defines PROVIDERS = [YourProvider]
plugins/<name>/tests/          pytest files, collected by the backend suite
```

The registry (`app/providers/registry.py`) scans this directory at startup, imports each
`provider.py`, and instantiates every class listed in `PROVIDERS`. A plugin that fails to
import is reported as `unavailable` in `/api/v1/sources` — it never prevents startup.

## Rules a plugin must follow

1. **Public sources only.** No authentication bypass, no CAPTCHA solving, no credential
   testing, no private-profile access. A provider that needs a third-party *user's*
   credentials does not belong here.
2. **Use `ctx.http`.** It is the SSRF-guarded client. Do not construct your own
   `httpx.AsyncClient`; doing so bypasses the egress policy.
3. **Be import-safe.** No network calls or binary probing at import time — put those in
   `health_check()`.
4. **Validate before interpolating.** Run any target value through `self.safe_value()`
   before it enters a URL path or a subprocess argv.
5. **Report honestly.** Emit `assertion="unverified"` for anything you could not
   corroborate, and pick the `MatchStrength` that reflects how directly you verified it.

See `docs/03-provider-architecture.md` for the full contract and a worked example.
