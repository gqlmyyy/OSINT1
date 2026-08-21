from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import (
    CurrentUser,
    InvestigationDep,
    PageDep,
    SessionDep,
    client_ip,
    require_permission,
)
from app.core.enums import InvestigationStatus, Role
from app.jobs.orchestrator import ScanRequest
from app.jobs.queue import get_queue
from app.models import Entity, IdentityCandidate, Investigation, Note, Observation, Relationship
from app.providers.registry import get_registry
from app.schemas.common import Page
from app.schemas.graph import (
    EntityOut,
    GraphOut,
    MatchOut,
    ObservationOut,
    RelationshipOut,
    SearchHit,
    SearchOut,
    TimelineEvent,
)
from app.schemas.investigation import (
    ExportRequest,
    InvestigationCreate,
    InvestigationOut,
    InvestigationUpdate,
    NoteIn,
    NoteOut,
    ProgressOut,
    ScanOut,
    ScanRequestIn,
    TargetOut,
    TargetsRequest,
)
from app.security import audit
from app.security.rbac import has_role
from app.services.export import ExportService
from app.services.investigation import InvestigationService
from app.services.search import SearchService
from app.services.source_registry import SourceRegistryService
from app.services.timeline import TimelineService

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("", response_model=InvestigationOut, status_code=status.HTTP_201_CREATED)
async def create_investigation(
    payload: InvestigationCreate,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    _: Annotated[object, require_permission("investigation:write")],
) -> dict[str, Any]:
    service = InvestigationService(session)
    investigation = await service.create(payload, user.id)
    await audit.record(
        session,
        action="investigation.create",
        actor_id=user.id,
        target=str(investigation.id),
        ip=client_ip(request),
    )
    return await _serialize(session, investigation)


@router.get("", response_model=Page[InvestigationOut])
async def list_investigations(
    session: SessionDep,
    user: CurrentUser,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[InvestigationStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    stmt = select(Investigation)
    if not has_role(user.role, Role.ADMIN):
        stmt = stmt.where(Investigation.owner_id == user.id)
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Investigation.name).like(pattern),
                func.lower(func.coalesce(Investigation.description, "")).like(pattern),
            )
        )
    if status_filter:
        stmt = stmt.where(Investigation.status == str(status_filter))

    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                stmt.order_by(Investigation.updated_at.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
    )
    items = [await _serialize(session, row) for row in rows]
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/{investigation_id}", response_model=InvestigationOut)
async def get_investigation_detail(
    session: SessionDep, investigation: InvestigationDep
) -> dict[str, Any]:
    return await _serialize(session, investigation)


@router.patch("/{investigation_id}", response_model=InvestigationOut)
async def update_investigation(
    payload: InvestigationUpdate,
    session: SessionDep,
    investigation: InvestigationDep,
    _: Annotated[object, require_permission("investigation:write")],
) -> dict[str, Any]:
    if payload.name is not None:
        investigation.name = payload.name
    if payload.description is not None:
        investigation.description = payload.description
    if payload.tags is not None:
        investigation.tags = list(dict.fromkeys(payload.tags))
    await session.flush()
    return await _serialize(session, investigation)


@router.delete("/{investigation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_investigation(
    session: SessionDep,
    investigation: InvestigationDep,
    request: Request,
    user: CurrentUser,
    _: Annotated[object, require_permission("investigation:delete")],
) -> Response:
    await InvestigationService(session).delete(investigation.id)
    await audit.record(
        session,
        action="investigation.delete",
        actor_id=user.id,
        target=str(investigation.id),
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{investigation_id}/targets", response_model=list[TargetOut])
async def add_targets(
    payload: TargetsRequest,
    session: SessionDep,
    investigation: InvestigationDep,
    _: Annotated[object, require_permission("investigation:write")],
) -> list[Any]:
    created, rejected = await InvestigationService(session).add_targets(
        investigation.id, payload.targets
    )
    if rejected and not created:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_targets", "message": "; ".join(rejected)},
        )
    return created


@router.post("/{investigation_id}/scan", response_model=ScanOut)
async def start_scan(
    payload: ScanRequestIn,
    session: SessionDep,
    investigation: InvestigationDep,
    request: Request,
    user: CurrentUser,
    _: Annotated[object, require_permission("scan:run")],
) -> ScanOut:
    service = InvestigationService(session)
    if not await service.target_types(investigation.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="add at least one target before scanning",
        )

    registry = get_registry()
    await SourceRegistryService(session, registry).sync()
    available = {p.name for p in registry.enabled()}
    requested = payload.providers or sorted(available)
    unknown = [name for name in requested if name not in available]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "unknown_provider", "message": f"not enabled: {', '.join(unknown)}"},
        )

    investigation.status = InvestigationStatus.RUNNING
    await session.commit()

    task_id = await get_queue().enqueue_scan(
        ScanRequest(
            investigation_id=investigation.id,
            providers=requested,
            max_depth=payload.max_depth,
            recursive=payload.recursive,
        )
    )
    await audit.record(
        session,
        action="investigation.scan",
        actor_id=user.id,
        target=str(investigation.id),
        ip=client_ip(request),
        meta={"providers": requested},
    )
    return ScanOut(
        investigation_id=investigation.id, queued=True, task_id=task_id, providers=requested
    )


@router.post("/{investigation_id}/cancel", response_model=ProgressOut)
async def cancel_scan(
    session: SessionDep,
    investigation: InvestigationDep,
    _: Annotated[object, require_permission("scan:run")],
) -> dict[str, Any]:
    await get_queue().cancel(investigation.id)
    investigation.status = InvestigationStatus.CANCELLED
    await session.flush()
    return await InvestigationService(session).progress(investigation)


@router.get("/{investigation_id}/progress", response_model=ProgressOut)
async def get_progress(session: SessionDep, investigation: InvestigationDep) -> dict[str, Any]:
    return await InvestigationService(session).progress(investigation)


@router.get("/{investigation_id}/entities", response_model=Page[EntityOut])
async def list_entities(
    session: SessionDep,
    investigation: InvestigationDep,
    page: PageDep,
    type: Annotated[str | None, Query(max_length=32)] = None,
    min_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    stmt = select(Entity).where(
        Entity.investigation_id == investigation.id, Entity.confidence >= min_confidence
    )
    if type:
        stmt = stmt.where(Entity.type == type)
    if q:
        stmt = stmt.where(func.lower(Entity.label).like(f"%{q.lower()}%"))
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                stmt.order_by(Entity.confidence.desc(), Entity.label)
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
    )
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/{investigation_id}/relationships", response_model=list[RelationshipOut])
async def list_relationships(
    session: SessionDep,
    investigation: InvestigationDep,
    type: Annotated[str | None, Query(max_length=32)] = None,
    min_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> list[Any]:
    stmt = select(Relationship).where(
        Relationship.investigation_id == investigation.id,
        Relationship.confidence >= min_confidence,
    )
    if type:
        stmt = stmt.where(Relationship.type == type)
    return list((await session.execute(stmt.order_by(Relationship.confidence.desc()))).scalars())


@router.get("/{investigation_id}/graph", response_model=GraphOut)
async def get_graph(
    session: SessionDep,
    investigation: InvestigationDep,
    min_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    types: Annotated[str | None, Query(max_length=500)] = None,
    edge_types: Annotated[str | None, Query(max_length=500)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    expand: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
) -> dict[str, Any]:
    from app.graph.projection import GraphFilters, GraphProjection

    filters = GraphFilters(
        min_confidence=min_confidence,
        entity_types={t.strip() for t in types.split(",") if t.strip()} if types else set(),
        relationship_types=(
            {t.strip() for t in edge_types.split(",") if t.strip()} if edge_types else set()
        ),
        since=since,
        until=until,
        expand=expand,
        query=q,
        limit=limit,
    )
    return await GraphProjection(session, investigation.id).build(filters)


@router.get("/{investigation_id}/timeline", response_model=list[TimelineEvent])
async def get_timeline(
    session: SessionDep,
    investigation: InvestigationDep,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[Any]:
    return await TimelineService(session, investigation.id).build(entity_id)


@router.get("/{investigation_id}/observations", response_model=Page[ObservationOut])
async def list_observations(
    session: SessionDep,
    investigation: InvestigationDep,
    page: PageDep,
    provider: Annotated[str | None, Query(max_length=64)] = None,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Observation).where(Observation.investigation_id == investigation.id)
    if provider:
        stmt = stmt.where(Observation.provider == provider)
    if entity_id:
        stmt = stmt.where(Observation.entity_id == entity_id)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                stmt.order_by(Observation.observed_at.desc()).limit(page.limit).offset(page.offset)
            )
        ).scalars()
    )
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/{investigation_id}/matches", response_model=list[MatchOut])
async def list_matches(
    session: SessionDep, investigation: InvestigationDep
) -> list[dict[str, Any]]:
    from app.correlation.engine import BAND_LABELS
    from app.core.enums import MatchBand

    rows = list(
        (
            await session.execute(
                select(IdentityCandidate)
                .where(IdentityCandidate.investigation_id == investigation.id)
                .order_by(IdentityCandidate.score.desc())
            )
        ).scalars()
    )
    labels = {
        e.id: e.label
        for e in (
            await session.execute(select(Entity).where(Entity.investigation_id == investigation.id))
        ).scalars()
    }
    return [
        {
            "id": row.id,
            "entity_a_id": row.entity_a_id,
            "entity_b_id": row.entity_b_id,
            "entity_a_label": labels.get(row.entity_a_id, ""),
            "entity_b_label": labels.get(row.entity_b_id, ""),
            "score": row.score,
            "band": row.band,
            "band_label": BAND_LABELS.get(MatchBand(row.band), row.band),
            "reasons": row.reasons,
            "explanation": row.explanation,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/{investigation_id}/search", response_model=SearchOut)
async def search_investigation(
    session: SessionDep,
    investigation: InvestigationDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchOut:
    hits = await SearchService(session).search(investigation.id, q, limit)
    return SearchOut(
        query=q, hits=[SearchHit.model_validate(h) for h in hits], total=len(hits)
    )


@router.post("/{investigation_id}/export")
async def export_investigation(
    payload: ExportRequest,
    session: SessionDep,
    investigation: InvestigationDep,
    request: Request,
    user: CurrentUser,
    _: Annotated[object, require_permission("export:create")],
) -> Response:
    content, media_type, filename = await ExportService(session, investigation.id).export(
        payload.format,
        payload.scope,
        entity_ids=payload.entity_ids,
        min_confidence=payload.min_confidence,
    )
    await audit.record(
        session,
        action="investigation.export",
        actor_id=user.id,
        target=str(investigation.id),
        ip=client_ip(request),
        meta={"format": str(payload.format), "scope": str(payload.scope)},
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{investigation_id}/notes", response_model=NoteOut, status_code=201)
async def add_note(
    payload: NoteIn,
    session: SessionDep,
    investigation: InvestigationDep,
    user: CurrentUser,
    _: Annotated[object, require_permission("investigation:write")],
) -> Note:
    note = Note(
        id=uuid.uuid4(),
        investigation_id=investigation.id,
        entity_id=payload.entity_id,
        author_id=user.id,
        body=payload.body,
    )
    session.add(note)
    await session.flush()
    return note


@router.get("/{investigation_id}/notes", response_model=list[NoteOut])
async def list_notes(session: SessionDep, investigation: InvestigationDep) -> list[Note]:
    return list(
        (
            await session.execute(
                select(Note)
                .where(Note.investigation_id == investigation.id)
                .order_by(Note.created_at.desc())
            )
        ).scalars()
    )


async def _serialize(session: Any, investigation: Investigation) -> dict[str, Any]:
    await session.refresh(investigation, ["targets"])
    stats = await InvestigationService(session).stats(investigation.id)
    return {
        "id": investigation.id,
        "name": investigation.name,
        "description": investigation.description,
        "status": investigation.status,
        "stage": investigation.stage,
        "tags": investigation.tags,
        "owner_id": investigation.owner_id,
        "created_at": investigation.created_at,
        "updated_at": investigation.updated_at,
        "targets": investigation.targets,
        "stats": stats,
    }
