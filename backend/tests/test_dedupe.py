"""Audit-time deduplication and merging."""

from __future__ import annotations

import pytest

from app.core.enums import EntityType, RelationshipType
from app.correlation.dedupe import DeduplicationService
from app.graph.store import GraphStore


async def _entity(store: GraphStore, key: str, label: str, entity_type=EntityType.SOCIAL_ACCOUNT):
    return (
        await store.upsert_entity(
            entity_type=entity_type, canonical_key=key, label=label, confidence=0.8,
            attributes={"platform": "GitHub"}, source="github",
        )
    ).entity


async def test_finds_same_type_entities_sharing_a_strong_identifier(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    a = await _entity(store, "github:user:a", "GitHub/a")
    b = await _entity(store, "github:user:b", "GitHub/b")
    for entity in (a, b):
        await store.add_identifier(entity, "email", "shared@example.com", "shared@example.com")

    groups = await DeduplicationService(session, investigation.id).find_duplicates()
    assert groups and groups[0].kind == "email"
    assert set(groups[0].entity_ids) == {a.id, b.id}


async def test_ignores_cross_type_identifier_sharing(session, investigation) -> None:
    """An account and an email node sharing an address is a relationship, not a duplicate."""
    store = GraphStore(session, investigation.id)
    account = await _entity(store, "github:user:a", "GitHub/a")
    email = await _entity(store, "email:shared@example.com", "shared@example.com", EntityType.EMAIL)
    for entity in (account, email):
        await store.add_identifier(entity, "email", "shared@example.com", "shared@example.com")

    assert await DeduplicationService(session, investigation.id).find_duplicates() == []


async def test_merge_moves_evidence_edges_and_identifiers(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    survivor = await _entity(store, "github:user:a", "GitHub/a")
    duplicate = await _entity(store, "github:user:a-dup", "GitHub/a (dup)")
    other = await _entity(store, "domain:example.com", "example.com", EntityType.DOMAIN)

    await store.add_identifier(duplicate, "email", "x@example.com", "x@example.com")
    await store.add_relationship(
        source=duplicate, target=other, rel_type=RelationshipType.LINKS_TO, confidence=0.8,
        provider="github", evidence={"observation_id": "obs-1", "why": "profile link"},
    )
    await session.flush()

    merged = await DeduplicationService(session, investigation.id).merge(survivor.id, duplicate.id)
    await session.flush()

    assert merged.id == survivor.id
    assert "github:user:a-dup" in merged.attributes["merged_from"]

    entities = await store.entities()
    assert {e.id for e in entities} == {survivor.id, other.id}

    edges = await store.relationships()
    assert len(edges) == 1
    assert edges[0].source_entity_id == survivor.id

    from sqlalchemy import select

    from app.models import Identifier

    kinds = {
        row.kind
        for row in (await session.execute(
            select(Identifier).where(Identifier.entity_id == survivor.id)
        )).scalars()
    }
    assert "email" in kinds


async def test_merge_collapses_edges_that_would_become_duplicates(session, investigation) -> None:
    store = GraphStore(session, investigation.id)
    survivor = await _entity(store, "github:user:a", "GitHub/a")
    duplicate = await _entity(store, "github:user:a2", "GitHub/a2")
    other = await _entity(store, "domain:example.com", "example.com", EntityType.DOMAIN)

    for entity in (survivor, duplicate):
        await store.add_relationship(
            source=entity, target=other, rel_type=RelationshipType.LINKS_TO, confidence=0.8,
            provider="github", evidence={"observation_id": f"obs-{entity.canonical_key}"},
        )
    assert len(await store.relationships()) == 2

    await DeduplicationService(session, investigation.id).merge(survivor.id, duplicate.id)
    await session.flush()
    assert len(await store.relationships()) == 1


async def test_merge_rejects_self_and_missing_entities(session, investigation) -> None:
    import uuid

    service = DeduplicationService(session, investigation.id)
    store = GraphStore(session, investigation.id)
    entity = await _entity(store, "github:user:a", "GitHub/a")

    with pytest.raises(ValueError, match="into itself"):
        await service.merge(entity.id, entity.id)
    with pytest.raises(LookupError):
        await service.merge(entity.id, uuid.uuid4())
