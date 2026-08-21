"""Observation -> entities, identifiers, relationships, and derived targets.

This module is the *only* writer of ``Assertion.OBSERVED``: everything it emits comes
straight from what a provider actually saw at a public source.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EntityType, RelationshipType, TargetType
from app.evidence import normalizer as norm
from app.evidence.canonical import CanonicalError, canonical_key, entity_type_for, label_for
from app.evidence.store import EvidenceStore
from app.graph.store import GraphStore
from app.models import Entity
from app.providers.types import Observation, Target

#: Identifier kinds recorded per entity type for search and correlation.
_IDENTIFIER_KINDS: dict[EntityType, str] = {
    EntityType.USERNAME: "username",
    EntityType.EMAIL: "email",
    EntityType.SOCIAL_ACCOUNT: "username",
    EntityType.DOMAIN: "domain",
    EntityType.WEBSITE: "domain",
    EntityType.URL: "url",
    EntityType.IP: "ip",
    EntityType.REPOSITORY: "repository",
    EntityType.PHONE: "phone",
    EntityType.AVATAR: "avatar",
    EntityType.CRYPTO_HASH: "hash",
}


@dataclass
class ExtractionResult:
    new_entities: list[Entity] = field(default_factory=list)
    touched_entities: list[Entity] = field(default_factory=list)
    new_relationships: list[Any] = field(default_factory=list)
    derived_targets: list[Target] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class EntityExtractor:
    def __init__(
        self,
        session: AsyncSession,
        investigation_id: uuid.UUID,
        graph: GraphStore | None = None,
    ) -> None:
        self.session = session
        self.investigation_id = investigation_id
        self.graph = graph or GraphStore(session, investigation_id)
        self.evidence = EvidenceStore(session, investigation_id)

    async def ingest(
        self,
        origin: Target,
        observations: list[Observation],
        *,
        job_id: uuid.UUID | None = None,
    ) -> ExtractionResult:
        result = ExtractionResult()
        origin_entity = await self._ensure_origin(origin, result)

        for observation in observations:
            try:
                await self._ingest_one(origin, origin_entity, observation, result, job_id)
            except (CanonicalError, norm.NormalizationError, ValueError) as exc:
                result.skipped.append(f"{observation.kind}:{observation.value}: {exc}")
        return result

    async def _ensure_origin(self, origin: Target, result: ExtractionResult) -> Entity:
        kind = _target_kind(origin.type)
        key = canonical_key(kind, origin.normalized)
        upsert = await self.graph.upsert_entity(
            entity_type=entity_type_for(kind),
            canonical_key=key,
            label=origin.value,
            confidence=0.99,
            attributes={"raw": origin.value, "is_target": True},
            source="target",
            depth=origin.depth,
        )
        if upsert.created:
            result.new_entities.append(upsert.entity)
        await self._index_identifiers(upsert.entity, kind, origin.normalized)
        return upsert.entity

    async def _ingest_one(
        self,
        origin: Target,
        origin_entity: Entity,
        observation: Observation,
        result: ExtractionResult,
        job_id: uuid.UUID | None,
    ) -> None:
        entity_type = entity_type_for(observation.kind)
        key = canonical_key(observation.kind, observation.value, observation.data)
        upsert = await self.graph.upsert_entity(
            entity_type=entity_type,
            canonical_key=key,
            label=observation.label
            or label_for(observation.kind, observation.value, observation.data),
            confidence=float(observation.confidence or 0.5),
            attributes=(
                {**observation.data, "url": observation.url}
                if observation.url
                else dict(observation.data)
            ),
            source=observation.provider,
            observed_at=observation.observed_at,
            depth=origin.depth + 1,
        )
        entity = upsert.entity
        (result.new_entities if upsert.created else result.touched_entities).append(entity)

        row = await self.evidence.record(observation, entity_id=entity.id, job_id=job_id)
        await self._index_identifiers(entity, observation.kind, observation.value, observation.data)

        evidence_payload = {
            "observation_id": str(row.id),
            "url": observation.url,
            "provider": observation.provider,
            "excerpt": observation.excerpt[:400],
            "why": _default_why(observation, origin),
        }

        if entity.id != origin_entity.id:
            edge = await self.graph.add_relationship(
                source=origin_entity,
                target=entity,
                rel_type=_edge_for(origin.type, entity_type),
                confidence=float(observation.confidence or 0.5),
                provider=observation.provider,
                assertion=observation.assertion,
                evidence=evidence_payload,
            )
            if edge.created:
                result.new_relationships.append(edge.relationship)

        for hint in observation.edges:
            await self._ingest_edge_hint(entity, hint, observation, evidence_payload, result)

        for kind, value in observation.derived_targets:
            derived = _to_target(kind, value, origin.depth + 1)
            if derived is not None:
                result.derived_targets.append(derived)

    async def _ingest_edge_hint(
        self,
        source_entity: Entity,
        hint: Any,
        observation: Observation,
        evidence_payload: dict[str, Any],
        result: ExtractionResult,
    ) -> None:
        try:
            target_type = entity_type_for(hint.target_kind)
            key = canonical_key(hint.target_kind, hint.target_value, hint.target_attributes)
        except (CanonicalError, norm.NormalizationError, ValueError) as exc:
            result.skipped.append(f"edge {hint.type} -> {hint.target_value}: {exc}")
            return

        upsert = await self.graph.upsert_entity(
            entity_type=target_type,
            canonical_key=key,
            label=label_for(hint.target_kind, hint.target_value, hint.target_attributes),
            confidence=float(observation.confidence or 0.5) * 0.95,
            attributes=dict(hint.target_attributes),
            source=observation.provider,
            observed_at=observation.observed_at,
            depth=source_entity.depth + 1,
        )
        (result.new_entities if upsert.created else result.touched_entities).append(upsert.entity)
        await self._index_identifiers(
            upsert.entity, hint.target_kind, hint.target_value, hint.target_attributes
        )
        if upsert.entity.id == source_entity.id:
            return
        head, tail = (
            (upsert.entity, source_entity) if getattr(hint, "reverse", False)
            else (source_entity, upsert.entity)
        )
        edge = await self.graph.add_relationship(
            source=head,
            target=tail,
            rel_type=hint.type,
            confidence=float(observation.confidence or 0.5) * 0.95,
            provider=observation.provider,
            assertion=observation.assertion,
            evidence={**evidence_payload, "why": hint.why or evidence_payload["why"]},
        )
        if edge.created:
            result.new_relationships.append(edge.relationship)

    async def _index_identifiers(
        self,
        entity: Entity,
        kind: str,
        value: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        attributes = attributes or {}
        entity_type = entity_type_for(kind)
        identifier_kind = _IDENTIFIER_KINDS.get(entity_type, "text")
        try:
            if entity_type is EntityType.SOCIAL_ACCOUNT:
                handle = str(attributes.get("username") or value)
                await self.graph.add_identifier(
                    entity, "username", handle, norm.normalize_username(handle)
                )
            elif entity_type is EntityType.EMAIL:
                await self.graph.add_identifier(
                    entity, "email", value, norm.normalize_email(value)
                )
            elif entity_type in (EntityType.DOMAIN, EntityType.WEBSITE):
                await self.graph.add_identifier(
                    entity, "domain", value, norm.normalize_domain(value)
                )
            elif entity_type is EntityType.URL:
                await self.graph.add_identifier(entity, "url", value, norm.normalize_url(value))
            else:
                await self.graph.add_identifier(
                    entity, identifier_kind, value, norm.normalize_text(value)
                )
        except norm.NormalizationError:
            await self.graph.add_identifier(entity, "text", value, norm.normalize_text(value))

        extra_identifiers = (
            ("email", "email"),
            ("avatar", "avatar_hash"),
            ("domain", "website_domain"),
        )
        for extra_kind, extra_key in extra_identifiers:
            raw = attributes.get(extra_key)
            if isinstance(raw, str) and raw:
                await self.graph.add_identifier(entity, extra_kind, raw, norm.normalize_text(raw))


def _target_kind(target_type: TargetType) -> str:
    mapping = {
        TargetType.USERNAME: "username",
        TargetType.EMAIL: "email",
        TargetType.DOMAIN: "domain",
        TargetType.URL: "url",
        TargetType.IP: "ip",
        TargetType.PHONE: "phone",
        TargetType.HASH: "hash",
        TargetType.DISPLAY_NAME: "display_name",
        TargetType.FULL_NAME: "full_name",
        TargetType.COMPANY: "company",
    }
    return mapping.get(target_type, "display_name")


def _edge_for(origin_type: TargetType, entity_type: EntityType) -> RelationshipType:
    if entity_type is EntityType.SOCIAL_ACCOUNT and origin_type is TargetType.USERNAME:
        return RelationshipType.USES_USERNAME
    if entity_type is EntityType.IP:
        return RelationshipType.RESOLVES_TO
    if entity_type is EntityType.REPOSITORY:
        return RelationshipType.AUTHORED
    if entity_type in (EntityType.URL, EntityType.WEBSITE, EntityType.DOMAIN):
        return RelationshipType.LINKS_TO
    if entity_type in (EntityType.EMAIL, EntityType.AVATAR, EntityType.USERNAME):
        return RelationshipType.ASSOCIATED_WITH
    return RelationshipType.ASSOCIATED_WITH


def _default_why(observation: Observation, origin: Target) -> str:
    return (
        f"{observation.provider} observed a public {observation.kind.replace('_', ' ')} "
        f"for {origin.type} '{origin.value}'."
    )


def _to_target(kind: str, value: str, depth: int) -> Target | None:
    try:
        target_type = TargetType(kind)
    except ValueError:
        return None
    try:
        detected, normalized = norm.normalize(value, target_type)
    except norm.NormalizationError:
        return None
    return Target(type=detected, value=value, normalized=normalized, depth=depth)
