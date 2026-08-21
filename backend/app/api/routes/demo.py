from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep, require_permission
from app.demo.seed import seed_demo_investigation
from app.services.investigation import InvestigationService

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/seed", status_code=status.HTTP_201_CREATED)
async def seed(
    session: SessionDep,
    user: CurrentUser,
    _: Annotated[object, require_permission("investigation:write")],
) -> dict[str, Any]:
    investigation = await seed_demo_investigation(session, user.id)
    stats = await InvestigationService(session).stats(investigation.id)
    return {
        "investigation_id": str(investigation.id),
        "name": investigation.name,
        "stats": stats,
        "offline": True,
    }
