from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import InvestigationDep, SessionDep
from app.graph.analytics import GraphAnalytics
from app.schemas.graph import PairRequest, PathOut, PathStepOut, ShortestPathRequest

router = APIRouter(prefix="/investigations/{investigation_id}/analytics", tags=["analytics"])


@router.post("/shortest-path", response_model=PathOut)
async def shortest_path(
    payload: ShortestPathRequest, session: SessionDep, investigation: InvestigationDep
) -> PathOut:
    analytics = GraphAnalytics(session, investigation.id)
    try:
        steps = await analytics.shortest_path(str(payload.source_id), str(payload.target_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PathOut(
        found=bool(steps),
        steps=[PathStepOut(**vars(step)) for step in steps],
        markdown=_as_markdown(steps),
    )


@router.post("/common-neighbors", response_model=list[dict[str, Any]])
async def common_neighbors(
    payload: PairRequest, session: SessionDep, investigation: InvestigationDep
) -> list[dict[str, Any]]:
    try:
        return await GraphAnalytics(session, investigation.id).common_neighbors(
            str(payload.a_id), str(payload.b_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/shared-identifiers", response_model=list[dict[str, str]])
async def shared_identifiers(
    payload: PairRequest, session: SessionDep, investigation: InvestigationDep
) -> list[dict[str, str]]:
    return await GraphAnalytics(session, investigation.id).shared_identifiers(
        str(payload.a_id), str(payload.b_id)
    )


@router.get("/centrality", response_model=list[dict[str, Any]])
async def centrality(
    session: SessionDep,
    investigation: InvestigationDep,
    metric: Annotated[str, Query(pattern="^(degree|betweenness|closeness)$")] = "degree",
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[dict[str, Any]]:
    return await GraphAnalytics(session, investigation.id).centrality(metric, limit)


@router.get("/components", response_model=list[dict[str, Any]])
async def components(session: SessionDep, investigation: InvestigationDep) -> list[dict[str, Any]]:
    return await GraphAnalytics(session, investigation.id).connected_components()


@router.get("/isolated", response_model=list[dict[str, Any]])
async def isolated(session: SessionDep, investigation: InvestigationDep) -> list[dict[str, Any]]:
    return await GraphAnalytics(session, investigation.id).isolated()


@router.get("/clusters", response_model=list[dict[str, Any]])
async def clusters(session: SessionDep, investigation: InvestigationDep) -> list[dict[str, Any]]:
    return await GraphAnalytics(session, investigation.id).clusters()


@router.get("/strong-correlations", response_model=list[dict[str, Any]])
async def strong_correlations(
    session: SessionDep,
    investigation: InvestigationDep,
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = 0.85,
) -> list[dict[str, Any]]:
    return await GraphAnalytics(session, investigation.id).strong_correlations(threshold)


@router.get("/duplicates", response_model=list[dict[str, Any]])
async def duplicates(session: SessionDep, investigation: InvestigationDep) -> list[dict[str, Any]]:
    return await GraphAnalytics(session, investigation.id).duplicate_identities()


def _as_markdown(steps: list[Any]) -> str:
    """A path an analyst can paste straight into a report (spec 38)."""
    if not steps:
        return "_No path found between these entities._"
    lines = ["**Path**", ""]
    for index, step in enumerate(steps):
        if index:
            confidence = f" ({step.via_confidence:.0%})" if step.via_confidence else ""
            lines.append(f" ↓ `{step.via}`{confidence}")
        lines.append(f"- **{step.label}** — _{step.type}_")
    return "\n".join(lines)
