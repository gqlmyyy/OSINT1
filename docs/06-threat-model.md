# GraphIntel OSINT — Security Threat Model

Method: STRIDE over the four trust boundaries. Every mitigation below names the module
that implements it and the test that proves it.

## Trust boundaries

```
 B1 browser ──► API            untrusted input, session theft, XSS/CSRF
 B2 API ──► worker/queue       task injection, privilege escalation
 B3 worker ──► internet        SSRF, DNS rebinding, malicious responses  ◄── highest risk
 B4 platform ──► plugin code   in-process untrusted-ish code, command injection
```

## B3 — Server-Side Request Forgery (the dominant risk)

The product's whole job is "fetch this URL an analyst typed", which is textbook SSRF.

| Attack | Mitigation | Where | Test |
|---|---|---|---|
| `http://127.0.0.1:6379/` target | scheme allowlist (`http`,`https`), port allowlist (80/443 + configured) | `security/ssrf.py:validate_url` | `test_ssrf.py::test_blocked_schemes_and_ports` |
| `http://192.168.1.1/` | every resolved IP checked against loopback/private/link-local/reserved/multicast/CGNAT | `ssrf.py:_assert_public_ip` | `test_ssrf.py::test_private_ranges_blocked` |
| `http://169.254.169.254/latest/meta-data/` | link-local blocked + explicit metadata-host denylist | same | `test_ssrf.py::test_metadata_endpoint_blocked` |
| Public host → 302 → `http://10.0.0.5/` | redirects followed **manually**, each hop re-validated, cap `max_redirects=5` | `ssrf.py:SafeAsyncClient.request` | `test_ssrf.py::test_redirect_to_private_blocked` |
| **DNS rebinding** (TTL 0, public on check → private on connect) | resolve once, connect **to the pinned IP** with `Host` header + `sni_hostname` extension; no second lookup happens | `ssrf.py:SafeAsyncClient._send_pinned` | `test_ssrf.py::test_ip_is_pinned_after_resolution` |
| `http://user:pass@evil/` credential smuggling | userinfo rejected | `validate_url` | ✔ |
| Decompression bomb / 4 GB body | streamed with `max_bytes` cap, abort on exceed | `SafeAsyncClient` | `test_ssrf.py::test_response_size_capped` |
| Slowloris egress | connect/read/total timeouts, provider-level `timeout_seconds` | runner + client | ✔ |
| `file://`, `gopher://`, `ftp://` | scheme allowlist | ✔ | ✔ |

`ALLOW_PRIVATE_NETWORKS=true` exists **only** for the test suite and is refused when
`ENV=production` (`core/config.py` validator, `test_config.py::test_prod_refuses_private_networks`).

### B3.1 — Untrusted image parsing (`plugins/image_geo`)

Downloading and decoding an arbitrary image is its own attack surface, separate from
SSRF (which governs *where* the request goes) — this is about what a malicious *body*
from an otherwise-legitimate host can do to the parser.

| Attack | Mitigation | Where | Test |
|---|---|---|---|
| Oversized response (multi-GB body) | the existing SSRF guard's `HTTP_MAX_BYTES` streaming cap raises `ResponseTooLarge` before the body is ever fully buffered; the provider catches it and reports a graceful `unavailable` observation rather than propagating a raw error | `security/ssrf.py` (guard), `image_geo/provider.py:search` | `test_image_geo.py::test_oversized_image_is_rejected_gracefully` |
| Decompression bomb (small file, huge decoded pixel buffer) | `MAX_PIXELS=40_000_000` enforced independently of Pillow's own `Image.MAX_IMAGE_PIXELS` guard, checked before full decode | `social/exif.py:extract_gps_exif` | `test_image_geo.py` (oversized/corrupt cases) |
| Corrupted or non-image bytes served as an image | `Image.verify()` structural check first; every Pillow exception is caught and normalized to one `UnsafeImage` type — the provider never has to enumerate Pillow's exception hierarchy, and a decode failure can never crash a scan | `social/exif.py` | `test_image_geo.py::test_corrupt_image_is_rejected_not_raised` |
| Malformed/out-of-range GPS tags (e.g. latitude > 90) | explicit range validation after DMS→decimal conversion; rejected rather than reported as a coordinate | `social/exif.py` | `test_image_geo.py` (GPS range case) |
| Non-image content-type served at an image URL | `content-type` checked before any decode is attempted | `image_geo/provider.py` | `test_image_geo.py::test_non_image_content_type_is_ignored` |

Two absence states are distinguished deliberately, because conflating them would read as
a false negative: **no EXIF at all** (most platforms strip it on upload — expected, not a
failure) versus **EXIF present but no GPS tags** (the capturing device had location
services off, or the platform stripped GPS specifically while keeping other metadata).
The observation's `note` field states which case applies, in plain language, rather than
leaving the UI to show a bare empty result either way.

The optional Beta non-biometric visual-analysis feature described in the platform's UI
brief (OCR/landmark/sign detection, à la Bellingcat's methodology) is **not implemented**
in this pass — see the project's own delivery notes for why, and note it explicitly
excludes anything resembling facial or person recognition (§ "Explicitly out of scope by
design" below), independent of whether it is ever built.

## B4 — Plugins & external tools

| Attack | Mitigation |
|---|---|
| Command injection via a target value into `maigret`/`sherlock`/`holehe` | `asyncio.create_subprocess_exec` with an **argv list**, `shell=False`; targets pre-validated against `^[A-Za-z0-9._@+-]{1,190}$`; binary resolved with `shutil.which`, never from user input |
| Runaway tool | hard timeout + `killpg` of the process group; temp CWD wiped after |
| Malicious plugin folder | plugins are **trusted code** — documented, loaded only from `PLUGINS_PATH`, never from an upload endpoint. There is no "install plugin from URL" API. Hard isolation (subprocess/WASM) is a stated non-goal for v1 |
| Plugin bypassing the SSRF guard with its own `httpx.AsyncClient` | `ProviderContext.http` is the sanctioned client; a lint rule + code review gate direct client construction, and the built-in plugins are audited |

## B1 — Browser ↔ API

| Threat | Mitigation |
|---|---|
| Credential stuffing on our own login | Argon2id (memory-hard) + per-account throttle + generic error |
| Token theft / XSS | React escapes by default; no `dangerouslySetInnerHTML`; strict CSP (`default-src 'self'`); provider-returned strings (bios, titles) are rendered as text only |
| CSRF | Bearer tokens in `Authorization` (not cookies) ⇒ no ambient authority; strict CORS allowlist |
| Broken access control | every route depends on `require_role(...)`; investigation ownership checked in `deps.py:get_investigation_for_user`; `test_authz.py` walks the route table |
| Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` |
| MIME sniffing | `X-Content-Type-Options: nosniff` |
| Enumeration via timing | uniform error messages, constant-ish work on login |
| Abuse / DoS | per-user + per-IP token bucket → `429 Retry-After` |

## B2 — API ↔ worker

| Threat | Mitigation |
|---|---|
| Task payload injection | jobs carry UUIDs only; the worker re-reads state from Postgres and re-validates |
| Graph explosion / queue bomb | `max_depth=3`, `max_entities=500`, `max_jobs=100` per investigation, enforced in `jobs/budget.py`; recursion loop-guard on `(provider, canonical_key)` seen-set (`test_orchestrator.py::test_recursion_loop_guard`) |
| Redis exposure | bound to the compose network, password via env, never published to host by default |

## Data protection

* **SQL injection** — SQLAlchemy Core/ORM parameter binding everywhere; the one raw
  fragment (PG `to_tsquery`) uses `plainto_tsquery(:q)` binding, never string formatting.
* **Secrets** — pulled from env only; `.env` is git-ignored; `SECRET_KEY` must be ≥32 chars
  and is refused if left at the default when `ENV=production`; API keys are masked in
  `/sources` output and in logs by a logging filter. This applies uniformly to every
  bearer-token/API-key-gated provider (`instagram`, `twitter`, `hibp`, and `tiktok`/
  `linkedin` once configured) — each is covered by its own
  `test_the_..._is_never_stored_in_evidence` test asserting the credential never appears
  in a stored `Observation.raw` or `.data`.
* **Breach data minimisation (`plugins/breach/hibp`)** — the provider reads and stores
  only a breach's name, title, date, and reporting source from the HIBP API response; it
  never reads HIBP's `Description` or `DataClasses` fields into any variable, let alone
  stores them, and never requests or could receive actual leaked credentials (HIBP's API
  does not return them to any caller). Enforced by a source-level policy test
  (`test_hibp.py::test_provider_never_reads_dataclasses_or_description_fields`) in
  addition to the runtime assertion that stored data contains none of it.
* **Audit logging** — `audit_logs` records actor, action, target, IP for auth events,
  scans, source changes, exports, and deletions.
* **PII** — investigations hold personal data about real people. `DELETE /investigations/{id}`
  hard-deletes all children; export is role-gated; the README states the operator's legal
  duty (lawful basis, minimisation, retention).

## Explicitly out of scope by design (§2)

No component performs CAPTCHA solving, authentication bypass, credential stuffing,
password brute-force, session-cookie theft, private-profile access, access-control bypass,
or vulnerability exploitation. The provider contract has no field to carry a third-party
*user* credential, and any plugin attempting these is a licence and policy violation of
this project.

**No facial recognition.** The platform deliberately builds no feature that matches an
individual's identity from a face — this is excluded as a matter of policy, not an
engineering gap to fill later: it is a stalking-enablement tool far more than a
legitimate investigative one, and no confidence band or evidence requirement makes that
acceptable. Where an image *is* useful evidence, matching stays non-biometric: EXIF GPS
(`plugins/image_geo`), file hashes, and post/caption context. Any future contribution
proposing face-matching against this codebase should be rejected on this ground alone.

## Residual risks (accepted, v1)

1. Plugins run in-process — a hostile plugin has full backend privileges. *Mitigation:
   treat `plugins/` as source code under review.*
2. `httpx`/`h11` request-smuggling class bugs are inherited from dependencies.
   *Mitigation: `pip-audit` in CI, pinned ranges.*
3. **Behind a mandated forward proxy** (`HTTPS_PROXY` and friends), the proxy performs
   name resolution, so IP pinning is disabled — every other check still runs on every
   request and every redirect hop, but rebinding protection then depends on the proxy.
   Covered by `test_ssrf.py::test_validation_still_runs_when_pinning_is_off`.
4. Correlation output can be wrong about real people. *Mitigation: no verdict is ever
   rendered as fact — bands, reasons, and evidence links are mandatory in every surface,
   API and UI alike.*
