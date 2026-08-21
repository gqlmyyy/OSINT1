# GraphIntel OSINT — Graph Data Model

## 1. Node types

| Type | `canonical_key` shape | Typical attributes |
|---|---|---|
| `person` | `person:<uuid>` (synthetic identity hub) | display_name, identity_confidence |
| `username` | `username:<normalized>` | raw, normalized, variants |
| `email` | `email:<lower>` | domain, gravatar_hash |
| `phone` | `phone:<e164>` | country, carrier_hint |
| `social_account` | `<platform>:user:<normalized>` | platform, url, display_name, bio, avatar |
| `domain` | `domain:<punycode>` | registrar, created, ns, status |
| `url` | `url:<sha1(canonical)>` | canonical_url, title, status_code |
| `ip` | `ip:<address>` | version, asn, reverse |
| `repository` | `<platform>:repo:<owner>/<name>` | stars, language, homepage |
| `organization` / `company` | `org:<normalized>` | url, location |
| `location` | `location:<normalized>` | raw |
| `image` / `avatar` | `avatar:<sha256>` | url, phash, bytes |
| `website` | `website:<host>` | title, generator |
| `crypto_hash` | `hash:<algo>:<hex>` | algo, subject |
| `technology` | `tech:<normalized>` | category, version |

`canonical_key` is computed by `app/evidence/canonical.py` and is the **sole** dedup key
(§18). Two providers reporting `github.com/Example_User` and `GitHub: example_user` both
resolve to `github:user:example_user` and land on one node.

## 2. Edge types

```
USES_USERNAME      username  → social_account | person
OWNS               person    → domain | repository | social_account
LINKS_TO           any       → url | website | social_account      (a public page links it)
MENTIONS           any       → any
AUTHORED           social_account → repository | url
CONTRIBUTES_TO     social_account → repository
RESOLVES_TO        domain    → ip
HOSTED_ON          website|domain → ip | organization
ASSOCIATED_WITH    any       → any            (generic, weakest)
SAME_AVATAR        entity    ↔ entity         correlation
SAME_EMAIL         entity    ↔ entity         correlation
SAME_USERNAME      entity    ↔ entity         correlation
REFERENCES         url       → any
REDIRECTS_TO       url       → url
CO_OCCURS_WITH     entity    ↔ entity         correlation
POSSIBLE_MATCH     entity    ↔ entity         identity candidate
```

Every edge carries, without exception (§39):

```json
{
  "type": "LINKS_TO",
  "confidence": 0.91,
  "assertion": "observed",
  "provider": "github",
  "evidence": {
    "observation_id": "obs_…",
    "url": "https://github.com/example_user",
    "excerpt": "blog: https://example.com",
    "why": "Public GitHub profile declares this website."
  },
  "created_at": "2026-03-18T09:12:44Z"
}
```

An edge with no `evidence.observation_id` **fails a database-level check and an assertion
in `GraphService.add_relationship`** — there is no relationship without evidence.

## 3. Assertion lattice

```
observed     provider returned this fact directly from a public source
   │         (only the evidence extractor may write it)
   ├─► correlated   two observations share a canonical identifier
   │                 (only the correlation engine may write it)
   ├─► inferred      derived by a rule/model, not seen in any source
   └─► unverified    reported by a source we could not corroborate
```

Promotion is one-way and only `unverified → observed` when a *second, independent*
provider observes the same fact. `inferred` is terminal.

## 4. Projection sent to the UI

```jsonc
{
  "nodes": [{
    "id": "ent_…", "type": "social_account", "label": "github/example_user",
    "confidence": 0.94, "cluster": "github", "degree": 5,
    "first_seen": "…", "last_seen": "…", "sources": ["github"],
    "collapsed": false, "attributes": { }
  }],
  "edges": [{ "id": "rel_…", "source": "ent_a", "target": "ent_b",
              "type": "LINKS_TO", "confidence": 0.91, "assertion": "observed" }],
  "clusters": [{ "id": "github", "label": "GitHub cluster", "size": 48 }],
  "stats": { "entities": 24, "relationships": 41, "sources": 13,
             "strong_correlations": 7, "possible_matches": 5 }
}
```

Above `settings.graph_cluster_threshold` nodes the projection returns **cluster nodes
only**; the client expands one cluster at a time via
`GET /investigations/{id}/graph?expand=<cluster>` (§30 lazy expansion).

## 5. Analytics (§38)

Computed on demand with NetworkX over the projected graph:

`shortest_path(a,b)` · `common_neighbors(a,b)` · `shared_identifiers(a,b)` ·
`centrality(degree|betweenness)` · `isolated()` · `connected_components()` ·
`clusters(louvain-style label propagation)` · `duplicate_identities()` ·
`strong_correlations(threshold)`

A path result is renderable as a report fragment and copyable as Markdown.
