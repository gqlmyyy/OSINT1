"""Domain enumerations shared by models, schemas, providers and the graph engine."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class TargetType(StrEnum):
    USERNAME = "username"
    EMAIL = "email"
    DOMAIN = "domain"
    URL = "url"
    IP = "ip"
    PHONE = "phone"
    DISPLAY_NAME = "display_name"
    FULL_NAME = "full_name"
    COMPANY = "company"
    HASH = "hash"


class EntityType(StrEnum):
    PERSON = "person"
    USERNAME = "username"
    EMAIL = "email"
    PHONE = "phone"
    SOCIAL_ACCOUNT = "social_account"
    DOMAIN = "domain"
    URL = "url"
    IP = "ip"
    REPOSITORY = "repository"
    ORGANIZATION = "organization"
    COMPANY = "company"
    LOCATION = "location"
    IMAGE = "image"
    AVATAR = "avatar"
    WEBSITE = "website"
    CRYPTO_HASH = "crypto_hash"
    TECHNOLOGY = "technology"
    # -- social intelligence layer --
    POST = "post"
    COMMENT = "comment"
    HASHTAG = "hashtag"


class RelationshipType(StrEnum):
    USES_USERNAME = "USES_USERNAME"
    OWNS = "OWNS"
    LINKS_TO = "LINKS_TO"
    MENTIONS = "MENTIONS"
    AUTHORED = "AUTHORED"
    CONTRIBUTES_TO = "CONTRIBUTES_TO"
    RESOLVES_TO = "RESOLVES_TO"
    HOSTED_ON = "HOSTED_ON"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    SAME_AVATAR = "SAME_AVATAR"
    SAME_EMAIL = "SAME_EMAIL"
    SAME_USERNAME = "SAME_USERNAME"
    REFERENCES = "REFERENCES"
    REDIRECTS_TO = "REDIRECTS_TO"
    CO_OCCURS_WITH = "CO_OCCURS_WITH"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    # -- social intelligence layer --
    COMMENTED_ON = "COMMENTED_ON"
    INTERACTS_WITH = "INTERACTS_WITH"
    REPLIED_TO = "REPLIED_TO"
    USES_HASHTAG = "USES_HASHTAG"
    SHARES_WEBSITE = "SHARES_WEBSITE"
    SHARES_USERNAME = "SHARES_USERNAME"


CORRELATION_EDGES: frozenset[RelationshipType] = frozenset(
    {
        RelationshipType.SAME_AVATAR,
        RelationshipType.SAME_EMAIL,
        RelationshipType.SAME_USERNAME,
        RelationshipType.SHARES_WEBSITE,
        RelationshipType.SHARES_USERNAME,
        RelationshipType.CO_OCCURS_WITH,
        RelationshipType.POSSIBLE_MATCH,
    }
)

#: Edges that record *public activity between two parties*. They are counted, never
#: scored as identity evidence: two accounts interacting is, if anything, weak evidence
#: that they are different people. Keeping these out of correlation is what stops the
#: platform manufacturing false "same person" verdicts from ordinary social activity.
INTERACTION_EDGES: frozenset[RelationshipType] = frozenset(
    {
        RelationshipType.COMMENTED_ON,
        RelationshipType.REPLIED_TO,
        RelationshipType.MENTIONS,
        RelationshipType.INTERACTS_WITH,
    }
)


class Assertion(StrEnum):
    """How strongly a statement is held. `inferred` is terminal and never promoted."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    CORRELATED = "correlated"
    UNVERIFIED = "unverified"


class InteractionStrength(StrEnum):
    """How much two parties publicly interact. Deliberately *not* a probability, and
    deliberately unrelated to :class:`MatchBand` — see docs/09."""

    SINGLE = "single"
    OCCASIONAL = "occasional"
    REGULAR = "regular"
    FREQUENT = "frequent"


class MatchBand(StrEnum):
    POSSIBLE = "possible"
    PROBABLE = "probable"
    STRONG = "strong"
    CONFIRMED = "confirmed"


class InvestigationStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvestigationStage(StrEnum):
    """User-visible pipeline stages (spec 37)."""

    IDLE = "idle"
    DISCOVERY = "discovery"
    VALIDATION = "validation"
    CORRELATION = "correlation"
    GRAPH_EXPANSION = "graph_expansion"
    ANALYSIS = "analysis"
    DONE = "done"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class ProviderType(StrEnum):
    USERNAME = "username"
    EMAIL = "email"
    REPOSITORY = "repository"
    SEARCH_ENGINE = "search_engine"
    DOMAIN = "domain"
    SOCIAL = "social"
    NETWORK = "network"
    IMAGE = "image"
    AGGREGATOR = "aggregator"


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class MatchStrength(StrEnum):
    """How directly a provider verified the thing it is reporting."""

    EXACT_ID = "exact_id"
    CLAIMED_LINK = "claimed_link"
    PATTERN_MATCH = "pattern_match"
    WEAK_HEURISTIC = "weak_heuristic"


MATCH_STRENGTH_WEIGHT: dict[str, float] = {
    MatchStrength.EXACT_ID: 1.0,
    MatchStrength.CLAIMED_LINK: 0.9,
    MatchStrength.PATTERN_MATCH: 0.6,
    MatchStrength.WEAK_HEURISTIC: 0.35,
}


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    HTML = "html"
    MARKDOWN = "markdown"
    GRAPHML = "graphml"


class ExportScope(StrEnum):
    INVESTIGATION = "investigation"
    ENTITIES = "entities"
    RELATIONSHIPS = "relationships"
    EVIDENCE = "evidence"
