# Social Intelligence Layer

How the platform becomes an *investigation* tool rather than a search tool, built **on**
the existing architecture rather than beside it.

## 1. What is reused unchanged

| Existing component | Why it already fits |
|---|---|
| `OSINTProvider` + plugin registry | a social platform is just another provider |
| `Observation` → `Evidence` chain | posts and comments are observations like any other |
| `EdgeHint` | a provider can already declare *"this entity relates to that one, because…"* — posts, comments and hashtags need **no new plumbing**, only new entity kinds |
| `GraphStore` dedup on `canonical_key` | one account seen by five providers stays one node |
| `CorrelationEngine` (log-odds) | identity resolution is unchanged; social signals are added to the existing signal set |
| auth, queue/worker, export, SSRF guard | untouched |

The layer adds **three entity kinds, six edge types, one new service, and providers**.
Nothing is forked.

## 2. The central distinction: interaction ≠ identity

This is the design decision the rest of the layer hangs on.

```
Target ──COMMENTED_ON(17 occurrences)── User A     "High public interaction"
Target ──POSSIBLE_MATCH(0.71)────────── GitHub/x   "Possible match, 3 signals"
```

They are **different questions with different evidence and different UI**, and mixing
them is how OSINT tools produce confident nonsense:

* **Interaction strength** — *how often do these two publicly interact?* Counted, never
  probabilistic. Rendered as `Occasional / Regular / Frequent`, with the count.
* **Identity confidence** — *are these the same person?* Log-odds over independent
  signals, rendered as `Possible / Probable / Strong / Confirmed`.

**Frequent interaction is deliberately *not* an identity signal.** If anything it is weak
evidence *against* identity: people do not usually comment on themselves. Feeding
interaction counts into identity scoring would manufacture false positives at scale,
which is exactly the failure mode this platform exists to avoid.

`InteractionLedger` therefore writes to `Relationship.evidence` and never to
`IdentityCandidate`. The separation is enforced by a test, not by convention.

## 3. New vocabulary

```
Entities   post · comment · hashtag        (joining account, username, email, domain, …)

Edges      AUTHORED       account → post
           COMMENTED_ON   account → post          carries occurrence count + strength
           REPLIED_TO     comment → comment
           USES_HASHTAG   account|post → hashtag
           SHARES_WEBSITE account ↔ account       correlation
           SHARES_USERNAME account ↔ account      correlation
```

Canonical keys keep dedup working across providers:

```
<platform>:post:<shortcode>          instagram:post:CyA1b2c3
<platform>:comment:<sha1(...)>       mastodon:comment:9f3c…
hashtag:<normalized>                 hashtag:opensource
```

## 4. Pipeline

```
Target (username | profile URL)
   │
   ▼  SocialProvider.search()          one provider per platform
Profile ──► bio  ──► mentions, hashtags, external links   (shared text extractor)
   │
   ├─► Posts ──► captions ──► mentions, hashtags, links
   │      │
   │      └─► Comments ──► commenter accounts
   │                          │
   │                          ▼  InteractionLedger
   │                    occurrence counts → strength band
   ▼
derived identifiers (usernames, domains, emails)
   │
   ▼  existing recursive discovery, budget-bounded, priority-ordered
cross-platform providers (github, username_enum, maigret, …)
   │
   ▼  existing CorrelationEngine (+ social signals)
Identity candidates, banded and explained
   │
   ▼
Graph · Timeline · Key Findings · Evidence
```

## 5. Instagram: what is honestly possible

Instagram gates essentially all profile, post and comment data behind authentication for
anonymous clients, and its terms prohibit automated collection. The brief's own rules
(§2, §29) say a provider must then report `unavailable` rather than work around it, and
that is what this one does.

So the provider is built in two halves:

* **Official API path** — Instagram Graph API / oEmbed, used when the operator supplies
  their own credentials. Declared `requires_api_key`; reports `unavailable` with an
  actionable message when unset.
* **No unauthenticated scraping path at all.** No login-wall evasion, no private
  endpoints, no CAPTCHA handling, no cookie reuse. There is no code to disable, because
  there is no code.

To keep the social pipeline **real and demonstrable rather than theoretical**, the same
layer ships a **Mastodon** provider: a genuinely public, documented, unauthenticated API
that returns profiles, posts, replies, mentions and hashtags. It exercises every part of
the pipeline end to end — so the layer is proven working, and Instagram becomes a
credentials question rather than an engineering one.

> Building an Instagram scraper that quietly returns nothing would have looked like more
> features and delivered less. This is the honest version.

## 6. Where the code lives

```
backend/app/social/            text.py  interactions.py  findings.py     ← new, small
backend/app/providers/social.py                                          ← SocialProvider base
plugins/social/mastodon/       provider.py                               ← public API
plugins/social/instagram/      provider.py                               ← official API only
```

The registry now discovers plugins nested one level deep, so `plugins/social/<platform>/`
works without any change to the graph engine, the database, or the frontend — the
requirement in §31.
