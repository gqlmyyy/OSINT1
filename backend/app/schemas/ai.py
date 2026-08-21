from __future__ import annotations

import uuid

from pydantic import Field

from app.schemas.common import StrictModel


class AIStatus(StrictModel):
    enabled: bool
    provider: str
    model: str
    reachable: bool
    detail: str = ""


class AIClaim(StrictModel):
    """Every AI statement is bound to stored evidence; unbound claims are dropped."""

    statement: str
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SummarizeRequest(StrictModel):
    focus_entity_id: uuid.UUID | None = None
    max_entities: int = Field(default=60, ge=1, le=300)


class SummaryOut(StrictModel):
    summary: str
    claims: list[AIClaim]
    grounded: bool
    model: str
    disclaimer: str


class ExplainMatchRequest(StrictModel):
    candidate_id: uuid.UUID
