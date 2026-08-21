"""Raw evidence persistence. Written once, never mutated."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.normalizer import sha256_hex
from app.models import Evidence, Observation as ObservationRow
from app.providers.types import Observation


class EvidenceStore:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def record(
        self,
        observation: Observation,
        *,
        entity_id: uuid.UUID | None,
        job_id: uuid.UUID | None = None,
    ) -> ObservationRow:
        row = ObservationRow(
            id=uuid.uuid4(),
            investigation_id=self.investigation_id,
            entity_id=entity_id,
            job_id=job_id,
            provider=observation.provider,
            source=observation.source,
            kind=observation.kind,
            url=observation.url[:2048] if observation.url else None,
            observed_at=observation.observed_at,
            confidence=float(observation.confidence or 0.5),
            assertion=str(observation.assertion),
            data={
                "value": observation.value,
                "label": observation.label,
                "match": str(observation.match),
                **observation.data,
            },
        )
        self.session.add(row)
        await self.session.flush()

        payload: dict[str, Any] = observation.raw or {"value": observation.value, **observation.data}
        digest = sha256_hex(json.dumps(payload, sort_keys=True, default=str))
        self.session.add(
            Evidence(
                id=uuid.uuid4(),
                observation_id=row.id,
                content_type="application/json",
                sha256=digest,
                payload=payload,
                excerpt=observation.excerpt[:2000],
                captured_at=observation.observed_at,
            )
        )
        await self.session.flush()
        return row
