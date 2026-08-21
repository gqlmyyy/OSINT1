from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep, require_permission
from app.core.enums import Role, TargetType
from app.jobs.orchestrator import ScanRequest
from app.jobs.queue import get_queue
from app.models import Entity, Investigation, Observation, Relationship
from app.schemas.graph import (
    EntityDetail,
    ObservationOut,
    RelationshipDetail,
    RelationshipOut,
    TimelineEvent,
)
from app.schemas.investigation import ScanOut
from app.security.rbac import has_role
from app.services.investigation import InvestigationService
from app.services.timeline import TimelineService

router = APIRouter(tags=["entities"])


async def _load_entity(session: Any, user: Any, entity_id: uuid.UUID) -> Entity:
    entity = await session.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    investigation = await session.get(Investigation, entity.investigation_id)
    if investigation is None or (
        investigation.owner_id != user.id and not has_role(user.role, Role.ADMIN)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    return entity


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(
    entity_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    entity = await _load_entity(session, user, entity_id)
    relationships = list(
        (
            await session.execute(
                select(Relationship).where(
                    or_(
                        Relationship.source_entity_id == entity.id,
                        Relationship.target_entity_id == entity.id,
                    )
                )
            )
        ).scalars()
    )
    observations = list(
        (
            await session.execute(
                select(Observation)
                .where(Observation.entity_id == entity.id)
                .order_by(Observation.observed_at.desc())
                .limit(25)
            )
        ).scalars()
    )
    count = int(
        (
            await session.execute(
                select(func.count(Observation.id)).where(Observation.entity_id == entity.id)
            )
        ).scalar_one()
    )
    links = sorted(
        {
            o.url
            for o in observations
            if o.url
        }
        | ({str(entity.attributes["url"])} if entity.attributes.get("url") else set())
    )
    return {
        **{c.name: getattr(entity, c.name) for c in entity.__table__.columns},
        "identifiers": entity.identifiers,
        "observation_count": count,
        "relationships": relationships,
        "external_links": links,
        "recent_observations": observations,
    }


@router.get("/entities/{entity_id}/relationships", response_model=list[RelationshipOut])
async def entity_relationships(
    entity_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[Relationship]:
    entity = await _load_entity(session, user, entity_id)
    return list(
        (
            await session.execute(
                select(Relationship).where(
                    or_(
                        Relationship.source_entity_id == entity.id,
                        Relationship.target_entity_id == entity.id,
                    )
                )
            )
        ).scalars()
    )


@router.get("/entities/{entity_id}/observations", response_model=list[ObservationOut])
async def entity_observations(
    entity_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[Observation]:
    entity = await _load_entity(session, user, entity_id)
    return list(
        (
            await session.execute(
                select(Observation)
                .where(Observation.entity_id == entity.id)
                .order_by(Observation.observed_at.desc())
            )
        ).scalars()
    )


@router.get("/entities/{entity_id}/timeline", response_model=list[TimelineEvent])
async def entity_timeline(
    entity_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[Any]:
    entity = await _load_entity(session, user, entity_id)
    return await TimelineService(session, entity.investigation_id).build(entity.id)


@router.post("/entities/{entity_id}/expand", response_model=ScanOut)
async def expand_entity(
    entity_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    _: Annotated[object, require_permission("entity:expand")],
) -> ScanOut:
    """Queue recursive discovery seeded from this node (depth-bounded)."""
    entity = await _load_entity(session, user, entity_id)
    target_value, target_type = _entity_as_target(entity)
    if target_value is None or target_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"entities of type '{entity.type}' cannot seed a new search",
        )

    from app.schemas.investigation import TargetIn

    service = InvestigationService(session)
    await service.add_targets(
        entity.investigation_id, [TargetIn(value=target_value, type=target_type)]
    )
    await session.commit()

    task_id = await get_queue().enqueue_scan(
        ScanRequest(investigation_id=entity.investigation_id, recursive=True)
    )
    return ScanOut(
        investigation_id=entity.investigation_id,
        queued=True,
        task_id=task_id,
        providers=[],
    )


@router.get("/relationships/{relationship_id}", response_model=RelationshipDetail)
async def get_relationship(
    relationship_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """The 'why does this relationship exist?' payload (spec 39)."""
    relationship = await session.get(Relationship, relationship_id)
    if relationship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="relationship not found")
    investigation = await session.get(Investigation, relationship.investigation_id)
    if investigation is None or (
        investigation.owner_id != user.id and not has_role(user.role, Role.ADMIN)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="relationship not found")

    source = await session.get(Entity, relationship.source_entity_id)
    target = await session.get(Entity, relationship.target_entity_id)
    observation = None
    observation_id = relationship.evidence.get("observation_id")
    if observation_id:
        observation = await session.get(Observation, uuid.UUID(str(observation_id)))

    return {
        **{c.name: getattr(relationship, c.name) for c in relationship.__table__.columns},
        "source_label": source.label if source else "",
        "target_label": target.label if target else "",
        "why": relationship.evidence.get("why", ""),
        "evidence_url": relationship.evidence.get("url"),
        "observation": observation,
    }


def _entity_as_target(entity: Entity) -> tuple[str | None, TargetType | None]:
    attributes = entity.attributes
    mapping: dict[str, tuple[str | None, TargetType | None]] = {
        "username": (entity.label, TargetType.USERNAME),
        "email": (entity.label, TargetType.EMAIL),
        "domain": (entity.label, TargetType.DOMAIN),
        "website": (entity.label, TargetType.DOMAIN),
        "url": (str(attributes.get("url") or entity.label), TargetType.URL),
        "ip": (entity.label, TargetType.IP),
        "phone": (entity.label, TargetType.PHONE),
        "social_account": (
            str(attributes.get("username") or "") or None,
            TargetType.USERNAME,
        ),
    }
    return mapping.get(entity.type, (None, None))
