from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import InvestigationDep, SessionDep, require_permission
from app.schemas.ai import AIStatus, ExplainMatchRequest, SummarizeRequest, SummaryOut
from app.services.ai import AIDisabled, AIService

router = APIRouter(tags=["ai"])


@router.get("/ai/status", response_model=AIStatus)
async def ai_status(session: SessionDep) -> dict[str, Any]:
    return await AIService(session).status()


@router.post("/investigations/{investigation_id}/ai/summarize", response_model=SummaryOut)
async def summarize(
    payload: SummarizeRequest,
    session: SessionDep,
    investigation: InvestigationDep,
    _: Annotated[object, require_permission("ai:use")],
) -> dict[str, Any]:
    try:
        return await AIService(session).summarize(
            investigation.id, max_entities=payload.max_entities
        )
    except AIDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/investigations/{investigation_id}/ai/explain-match", response_model=SummaryOut)
async def explain_match(
    payload: ExplainMatchRequest,
    session: SessionDep,
    investigation: InvestigationDep,
    _: Annotated[object, require_permission("ai:use")],
) -> dict[str, Any]:
    return await AIService(session).explain_match(payload.candidate_id)
