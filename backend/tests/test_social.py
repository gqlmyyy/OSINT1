"""The social intelligence layer: extraction, interaction accounting, and findings.

The load-bearing test here is `test_interaction_never_becomes_identity_evidence`. Every
other feature could regress and the tool would still be usable; if that one regresses,
the platform starts telling people that two accounts are the same person because they
talk to each other, which is the failure mode it exists to prevent.
"""

from __future__ import annotations

import pytest

from app.core.enums import EntityType, InteractionStrength, RelationshipType
from app.correlation.engine import CorrelationEngine
from app.evidence.canonical import canonical_key, normalize_hashtag
from app.evidence.extractor import EntityExtractor
from app.providers.types import Target
from app.social.findings import FindingsService
from app.social.interactions import InteractionLedger, strength_for
from app.social.text import extract

TARGET = Target(type="username", value="example_user", normalized="example_user", depth=0)


# -- text extraction ----------------------------------------------------------


def test_extracts_handles_tags_links_and_addresses() -> None:
    found = extract("hi @alice and @Bob_2 #OpenSource #osint https://example.com me@example.com")
    assert found.mentions == ["alice", "bob_2"]
    assert found.hashtags == ["opensource", "osint"]
    assert found.urls == ["https://example.com"]
    assert found.emails == ["me@example.com"]


def test_email_domain_is_not_mistaken_for_a_handle() -> None:
    """`a@b.com` must not produce a mention of `b` — a classic source of graph noise."""
    assert extract("write to analyst@example.com").mentions == []


def test_platform_boilerplate_handles_are_dropped() -> None:
    """@everyone on every post would become a hub node connected to everything."""
    found = extract("@everyone @here look at this, thanks @realuser")
    assert found.mentions == ["realuser"]


@pytest.mark.parametrize("raw", ["#OpenSource", "opensource", "#openSOURCE"])
def test_hashtags_normalise_to_one_node(raw: str) -> None:
    assert normalize_hashtag(raw) == "opensource"
    assert canonical_key("hashtag", raw) == "hashtag:opensource"


def test_empty_and_oversized_text_are_safe() -> None:
    assert extract(None).is_empty()
    assert extract("").is_empty()
    extract("a" * 500_000)  # must not hang or raise


# -- canonical keys -----------------------------------------------------------


def test_the_same_post_from_two_providers_is_one_node() -> None:
    from_post = canonical_key("post", "https://m.test/@a/1", {"platform": "Mastodon", "post_id": "1"})
    from_comment = canonical_key(
        "post", "https://m.test/@a/1/different-url", {"platform": "mastodon", "post_id": "1"}
    )
    assert from_post == from_comment == "mastodon:post:1"


def test_different_comments_never_collapse() -> None:
    base = {"platform": "mastodon", "author": "bob", "post_key": "p1"}
    assert canonical_key("comment", "nice", base) != canonical_key("comment", "great", base)
    # ...but the same comment captured twice does.
    assert canonical_key("comment", "nice", base) == canonical_key("comment", "nice", dict(base))


# -- interaction accounting ---------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, InteractionStrength.SINGLE),
        (3, InteractionStrength.OCCASIONAL),
        (7, InteractionStrength.REGULAR),
        (40, InteractionStrength.FREQUENT),
    ],
)
def test_strength_bands(count: int, expected: InteractionStrength) -> None:
    assert strength_for(count) == expected


async def _seed_conversation(session, investigation, comments: int = 5):
    """Target posts; a commenter replies `comments` times across separate posts."""
    from app.providers.social import SocialProvider

    class Stub(SocialProvider):
        name = "stub"
        platform = "Mastodon"

        def capabilities(self):  # pragma: no cover - not exercised
            raise NotImplementedError

        async def health_check(self):  # pragma: no cover
            raise NotImplementedError

        async def search(self, target, ctx):  # pragma: no cover
            raise NotImplementedError

    provider = Stub()
    observations = [
        provider.profile_observation(
            username="example_user", url="https://m.test/@example_user", bio="hi"
        )
    ]
    for index in range(comments):
        post_url = f"https://m.test/@example_user/{index}"
        observations.append(
            provider.post_observation(
                author="example_user", post_id=str(index), url=post_url, caption="a post"
            )
        )
        observations.append(
            provider.comment_observation(
                author="commenter",
                text="nice work",
                post_key=str(index),
                post_url=post_url,
                comment_id=f"c{index}",
            )
        )
    for observation in observations:
        observation.confidence = observation.scored(0.9)

    await EntityExtractor(session, investigation.id).ingest(TARGET, observations)
    await session.flush()
    return provider


async def test_comments_are_counted_against_the_post_author(session, investigation) -> None:
    await _seed_conversation(session, investigation, comments=5)
    summaries = await InteractionLedger(session, investigation.id).build()

    assert summaries, "a commenter replying to the target's posts must be recorded"
    top = summaries[0]
    assert top.actor_label.endswith("commenter")
    assert top.target_label.endswith("example_user")
    assert top.comments == 5
    assert top.strength is InteractionStrength.REGULAR
    assert "Identity match not established" in top.describe()


async def test_interaction_never_becomes_identity_evidence(session, investigation) -> None:
    """The rule the whole layer is built around.

    Two accounts that interact constantly are not thereby the same person — if anything
    the reverse. Nothing the ledger writes may reach the correlation engine.
    """
    await _seed_conversation(session, investigation, comments=40)
    ledger = InteractionLedger(session, investigation.id)
    summaries = await ledger.materialize()
    await session.flush()

    assert summaries[0].strength is InteractionStrength.FREQUENT
    assert summaries[0].total == 40

    candidates = await CorrelationEngine().correlate_investigation(session, investigation.id)
    pairs = {
        frozenset({a.entity_a.label, a.entity_b.label})
        for a in candidates
    }
    commenter_and_target = frozenset({"Mastodon/commenter", "Mastodon/example_user"})
    assert commenter_and_target not in pairs, (
        "40 public comments produced an identity candidate — interaction volume has "
        "leaked into identity scoring"
    )


async def test_interaction_edge_states_that_identity_is_not_established(
    session, investigation
) -> None:
    await _seed_conversation(session, investigation, comments=6)
    await InteractionLedger(session, investigation.id).materialize()
    await session.flush()

    from app.graph.store import GraphStore

    edges = await GraphStore(session, investigation.id).relationships()
    summary_edges = [e for e in edges if e.type == RelationshipType.INTERACTS_WITH]
    assert summary_edges, "the ledger must record its conclusion as an explained edge"
    evidence = summary_edges[0].evidence
    assert evidence["identity_match"] == "not established"
    assert evidence["occurrences"] == 6
    assert evidence["observation_id"], "an edge without evidence is not allowed"


# -- graph shape --------------------------------------------------------------


async def test_social_pipeline_builds_the_expected_graph(session, investigation) -> None:
    await _seed_conversation(session, investigation, comments=3)
    from app.graph.store import GraphStore

    store = GraphStore(session, investigation.id)
    entities = await store.entities()
    types = {e.type for e in entities}
    assert EntityType.POST in types
    assert EntityType.SOCIAL_ACCOUNT in types

    edge_types = {e.type for e in await store.relationships()}
    assert RelationshipType.AUTHORED in edge_types
    assert RelationshipType.COMMENTED_ON in edge_types


async def test_authored_runs_from_account_to_post(session, investigation) -> None:
    """Direction matters: the ledger finds a post's author by following AUTHORED."""
    await _seed_conversation(session, investigation, comments=1)
    from app.graph.store import GraphStore

    store = GraphStore(session, investigation.id)
    entities = {e.id: e for e in await store.entities()}
    authored = [e for e in await store.relationships() if e.type == RelationshipType.AUTHORED]

    assert authored
    for edge in authored:
        assert entities[edge.source_entity_id].type == EntityType.SOCIAL_ACCOUNT
        assert entities[edge.target_entity_id].type == EntityType.POST


async def test_hashtags_and_mentions_in_a_caption_become_nodes(session, investigation) -> None:
    from app.providers.social import SocialProvider

    class Stub(SocialProvider):
        name = "stub"
        platform = "Mastodon"

        def capabilities(self):  # pragma: no cover
            raise NotImplementedError

        async def health_check(self):  # pragma: no cover
            raise NotImplementedError

        async def search(self, target, ctx):  # pragma: no cover
            raise NotImplementedError

    observation = Stub().post_observation(
        author="example_user",
        post_id="1",
        url="https://m.test/@example_user/1",
        caption="shipping #osint with @alice see https://example.com",
    )
    observation.confidence = observation.scored(0.9)
    await EntityExtractor(session, investigation.id).ingest(TARGET, [observation])
    await session.flush()

    from app.graph.store import GraphStore

    entities = await GraphStore(session, investigation.id).entities()
    keys = {e.canonical_key for e in entities}
    assert "hashtag:osint" in keys
    assert "mastodon:user:alice" in keys


# -- findings ------------------------------------------------------------------


async def test_findings_separate_identity_from_interaction(session, investigation) -> None:
    await _seed_conversation(session, investigation, comments=20)
    await InteractionLedger(session, investigation.id).materialize()
    await CorrelationEngine().correlate_investigation(session, investigation.id)
    await session.flush()

    result = await FindingsService(session, investigation.id).build()
    summary = result["summary"]
    assert summary["accounts"] >= 2
    assert summary["posts"] == 20
    assert summary["public_interactions"] == 20

    kinds = {f["kind"] for f in result["findings"]}
    assert "interaction" in kinds
    interaction = next(f for f in result["findings"] if f["kind"] == "interaction")
    assert "Identity match not established" in interaction["detail"]
    assert interaction["metrics"]["interactions"] == 20
    # An interaction finding must never be dressed up as an identity conclusion.
    assert interaction["kind"] != "identity"
    assert "same person" not in interaction["detail"].lower()
