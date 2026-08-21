from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.core.enums import ExportFormat, ExportScope, TargetType
from app.schemas.common import ORMModel, StrictModel


class TargetIn(StrictModel):
    value: str = Field(min_length=1, max_length=512)
    type: TargetType | None = None


class TargetsRequest(StrictModel):
    targets: list[TargetIn] = Field(min_length=1, max_length=50)


class TargetOut(ORMModel):
    id: uuid.UUID
    type: str
    value: str
    normalized: str
    created_at: datetime


class InvestigationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    targets: list[TargetIn] = Field(default_factory=list, max_length=50)


class InvestigationUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=32)


class InvestigationOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    stage: str
    tags: list[str]
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    targets: list[TargetOut] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class ScanRequestIn(StrictModel):
    providers: list[str] | None = Field(default=None, max_length=64)
    max_depth: int | None = Field(default=None, ge=0, le=5)
    recursive: bool = True


class ScanOut(StrictModel):
    investigation_id: uuid.UUID
    queued: bool
    task_id: str
    providers: list[str]


class ProviderProgress(StrictModel):
    provider: str
    total: int
    done: int
    failed: int
    percent: int
    status: str


class ProgressOut(StrictModel):
    investigation_id: uuid.UUID
    status: str
    stage: str
    percent: int
    providers: list[ProviderProgress]
    correlation_percent: int


class NoteIn(StrictModel):
    body: str = Field(min_length=1, max_length=8000)
    entity_id: uuid.UUID | None = None


class NoteOut(ORMModel):
    id: uuid.UUID
    body: str
    entity_id: uuid.UUID | None
    author_id: uuid.UUID | None
    created_at: datetime


class ExportRequest(StrictModel):
    format: ExportFormat
    scope: ExportScope = ExportScope.INVESTIGATION
    entity_ids: list[uuid.UUID] | None = Field(default=None, max_length=5000)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
