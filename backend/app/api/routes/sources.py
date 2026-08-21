from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, SessionDep, client_ip, require_permission
from app.providers.registry import get_registry
from app.schemas.source import SourceOut, SourceUpdate
from app.security import audit
from app.services.source_registry import SourceRegistryService

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
async def list_sources(
    session: SessionDep, _: Annotated[object, require_permission("source:read")]
) -> list[dict[str, Any]]:
    return await SourceRegistryService(session).listing()


@router.patch("/{name}", response_model=SourceOut)
async def update_source(
    name: str,
    payload: SourceUpdate,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
    _: Annotated[object, require_permission("source:write")],
) -> dict[str, Any]:
    service = SourceRegistryService(session)
    await service.sync()
    try:
        await service.update(name, payload.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await audit.record(
        session,
        action="source.update",
        actor_id=user.id,
        target=name,
        ip=client_ip(request),
        meta=payload.model_dump(exclude_unset=True, exclude={"config"}),
    )
    listing = await service.listing()
    return next(item for item in listing if item["name"] == name)


@router.post("/{name}/health", response_model=SourceOut)
async def check_health(
    name: str, session: SessionDep, _: Annotated[object, require_permission("source:read")]
) -> dict[str, Any]:
    registry = get_registry()
    entry = registry.get(name)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown source: {name}")
    listing = await SourceRegistryService(session, registry).listing()
    return next(item for item in listing if item["name"] == name)
