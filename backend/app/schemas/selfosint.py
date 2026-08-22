"""Wire contracts for the self-audit endpoints.

There is deliberately **no field anywhere in this module that can carry an access
token**. The response models are built by hand rather than from the ORM row precisely so
that adding a column to ``LinkedAccount`` can never start leaking it through the API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConnectStart(BaseModel):
    """Where to send the user's browser to grant consent."""

    authorize_url: str
    #: Echoed so the UI can explain what is being requested before redirecting.
    scopes: list[str] = Field(default_factory=list)
    provider: str = "instagram"


class CallbackIn(BaseModel):
    """What the SPA posts back after Meta redirects the user."""

    code: str = Field(min_length=1, max_length=1024)
    state: str = Field(min_length=1, max_length=512)


class CapabilityOut(BaseModel):
    key: str
    label: str
    availability: str
    note: str = ""
    requires_scope: str = ""


class LinkedAccountOut(BaseModel):
    """Everything the frontend is allowed to know about a connection."""

    connected: bool
    provider: str = "instagram"
    username: str | None = None
    account_type: str | None = None
    #: The platform's account id. Safe to show its owner; never accepted as *input*.
    provider_account_id: str | None = None
    token_state: str | None = None
    token_state_detail: str = ""
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    investigation_id: str | None = None
    sync_meta: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[CapabilityOut] = Field(default_factory=list)
    #: False when the operator has not configured Meta app credentials at all.
    oauth_configured: bool = True
    revocation_instructions: str = ""


class SyncOut(BaseModel):
    synced: bool
    observations: int = 0
    entities: int = 0
    relationships: int = 0
    external_providers_run: int = 0
    errors: list[str] = Field(default_factory=list)
    last_synced_at: datetime | None = None


class EvidenceRefOut(BaseModel):
    label: str
    source: str
    url: str | None = None
    entity_id: str | None = None
    observation_id: str | None = None


class FindingOut(BaseModel):
    category: str
    severity: str
    title: str
    description: str
    mitigation: str
    confidence: float
    availability: str
    evidence: list[EvidenceRefOut] = Field(default_factory=list)


class CorrelationOut(BaseModel):
    signal: str
    source: str
    confidence: float
    explanation: str
    identity_claim: bool
    evidence: list[EvidenceRefOut] = Field(default_factory=list)


class CategoryScoreOut(BaseModel):
    category: str
    score: int
    contributions: list[dict[str, Any]] = Field(default_factory=list)


class ScoreOut(BaseModel):
    overall: int
    level: str
    categories: list[CategoryScoreOut] = Field(default_factory=list)
    explanation: str = ""


class ReportOut(BaseModel):
    account: LinkedAccountOut
    findings: list[FindingOut] = Field(default_factory=list)
    correlations: list[CorrelationOut] = Field(default_factory=list)
    score: ScoreOut
    counts: dict[str, int] = Field(default_factory=dict)
    generated_at: datetime


class DisconnectOut(BaseModel):
    disconnected: bool
    remote_revocation: bool
    instructions: str


class DeleteOut(BaseModel):
    deleted: bool
    removed: dict[str, int] = Field(default_factory=dict)
