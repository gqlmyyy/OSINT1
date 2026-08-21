"""Dedup and the no-edge-without-evidence invariant."""

from __future__ import annotations

import pytest

from app.core.enums import Assertion, EntityType, RelationshipType
from app.graph.store import EvidenceRequired, GraphStore


async def test_repeat_sighting_does_not_create_a_second_node(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    first = await store.upsert_entity(
        entity_type=EntityType.SOCIAL_ACCOUNT,
        canonical_key="github:user:example_user",
        label="GitHub/example_user",
        confidence=0.8,
        attributes={"platform": "GitHub"},
        source="github",
    )
    second = await store.upsert_entity(
        entity_type=EntityType.SOCIAL_ACCOUNT,
        canonical_key="github:user:example_user",
        label="GitHub/example_user",
        confidence=0.7,
        attributes={"bio": "engineer"},
        source="username_enum",
    )
    third = await store.upsert_entity(
        entity_type=EntityType.SOCIAL_ACCOUNT,
        canonical_key="github:user:example_user",
        label="GitHub/example_user",
        confidence=0.6,
        source="maigret",
    )
    assert first.created and not second.created and not third.created
    assert first.entity.id == second.entity.id == third.entity.id
    assert len(await store.entities()) == 1
    assert first.entity.attributes["bio"] == "engineer"
    assert sorted(first.entity.sources) == ["github", "maigret", "username_enum"]


async def test_corroboration_raises_confidence_without_reaching_certainty(
    session, investigation
) -> None:
    store = GraphStore(session, investigation.id)
    for _ in range(20):
        result = await store.upsert_entity(
            entity_type=EntityType.USERNAME,
            canonical_key="username:example_user",
            label="example_user",
            confidence=0.8,
            source="p",
        )
    assert 0.8 < result.entity.confidence <= 0.99


async def test_relationship_requires_evidence(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    a = (await store.upsert_entity(
        entity_type=EntityType.USERNAME, canonical_key="username:a", label="a", confidence=0.9
    )).entity
    b = (await store.upsert_entity(
        entity_type=EntityType.DOMAIN, canonical_key="domain:b.com", label="b.com", confidence=0.9
    )).entity

    with pytest.raises(EvidenceRequired):
        await store.add_relationship(
            source=a, target=b, rel_type=RelationshipType.LINKS_TO,
            confidence=0.9, provider="x", evidence={},
        )
    with pytest.raises(EvidenceRequired):
        await store.add_relationship(
            source=a, target=b, rel_type=RelationshipType.LINKS_TO,
            confidence=0.9, provider="x", evidence={"why": "because"},
        )


async def test_duplicate_edges_merge_and_accumulate_corroboration(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    a = (await store.upsert_entity(
        entity_type=EntityType.USERNAME, canonical_key="username:a", label="a", confidence=0.9
    )).entity
    b = (await store.upsert_entity(
        entity_type=EntityType.DOMAIN, canonical_key="domain:b.com", label="b.com", confidence=0.9
    )).entity

    first = await store.add_relationship(
        source=a, target=b, rel_type=RelationshipType.LINKS_TO, confidence=0.6,
        provider="github", evidence={"observation_id": "obs-1", "why": "profile link"},
    )
    second = await store.add_relationship(
        source=a, target=b, rel_type=RelationshipType.LINKS_TO, confidence=0.6,
        provider="website", evidence={"observation_id": "obs-2", "why": "site footer"},
    )
    assert first.created and not second.created
    assert len(await store.relationships()) == 1
    assert second.relationship.confidence > 0.6
    assert second.relationship.evidence["corroborations"][0]["observation_id"] == "obs-2"


async def test_self_loops_are_rejected(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    a = (await store.upsert_entity(
        entity_type=EntityType.USERNAME, canonical_key="username:a", label="a", confidence=0.9
    )).entity
    with pytest.raises(ValueError, match="self-loops"):
        await store.add_relationship(
            source=a, target=a, rel_type=RelationshipType.ASSOCIATED_WITH, confidence=0.5,
            provider="x", evidence={"observation_id": "obs-1"}, assertion=Assertion.OBSERVED,
        )


async def test_website_and_domain_for_one_host_are_a_single_node(session, investigation) -> None:
    """Two providers describing the same host must not produce two lookalike nodes."""
    from app.evidence.extractor import EntityExtractor
    from app.providers.types import Observation, Target

    extractor = EntityExtractor(session, investigation.id)
    target = Target(type="domain", value="example.com", normalized="example.com", depth=0)
    await extractor.ingest(
        target,
        [
            Observation(
                provider="website", kind="website", value="example.com", confidence=0.9,
                url="https://example.com", data={"title": "Example", "generator": "Hugo"},
            ),
            Observation(
                provider="dns", kind="domain", value="example.com", confidence=0.95,
                data={"record": "NS", "role": "ns"},
            ),
        ],
    )
    await session.flush()

    store = GraphStore(session, investigation.id)
    hosts = [e for e in await store.entities() if e.canonical_key == "domain:example.com"]
    assert len(hosts) == 1, "the same host reported as website and domain must be one node"
    assert hosts[0].attributes["title"] == "Example", "website attributes are merged in"
    assert sorted(hosts[0].sources) == ["dns", "target", "website"]
