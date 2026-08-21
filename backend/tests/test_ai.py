"""The AI layer must stay grounded and must be optional."""

from __future__ import annotations

import json
import uuid

import pytest

from app.services.ai import AIDisabled, AIService, _parse_json


async def test_disabled_by_default(session) -> None:
    service = AIService(session)
    assert service.enabled is False
    status = await service.status()
    assert status["enabled"] is False
    with pytest.raises(AIDisabled):
        await service.summarize(uuid.uuid4())


async def test_ungrounded_claims_are_dropped(session, user, monkeypatch) -> None:
    """A claim citing an id that is not in the evidence set never reaches the caller."""
    from app.demo.seed import seed_demo_investigation

    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    service = AIService(session)
    monkeypatch.setattr(service.settings, "ai_enabled", True)
    monkeypatch.setattr(service.settings, "llm_provider", "ollama")

    context, valid_ids = await service._build_context(investigation.id, 60)
    real_id = str(next(iter(valid_ids)))

    async def fake_complete(_payload: str) -> str:
        return json.dumps(
            {
                "summary": "Two public accounts share a declared website.",
                "claims": [
                    {"statement": "grounded claim", "evidence_ids": [real_id], "confidence": 0.8},
                    {
                        "statement": "invented claim",
                        "evidence_ids": [str(uuid.uuid4())],
                        "confidence": 0.99,
                    },
                    {"statement": "uncited claim", "evidence_ids": [], "confidence": 0.99},
                ],
            }
        )

    monkeypatch.setattr(service, "_complete", fake_complete)
    result = await service.summarize(investigation.id)

    statements = [c["statement"] for c in result["claims"]]
    assert statements == ["grounded claim"]
    assert result["grounded"] is True
    assert "reading aid" in result["disclaimer"]


async def test_explain_match_falls_back_to_the_stored_reasons(session, user) -> None:
    """With AI off, the explanation is the deterministic one — never absent."""
    from sqlalchemy import select

    from app.demo.seed import seed_demo_investigation
    from app.models import IdentityCandidate

    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    candidate = (await session.execute(
        select(IdentityCandidate).where(
            IdentityCandidate.investigation_id == investigation.id
        )
    )).scalars().first()
    assert candidate is not None

    result = await AIService(session).explain_match(candidate.id)
    assert result["model"] == "deterministic"
    assert result["summary"] == candidate.explanation
    assert len(result["claims"]) == len(candidate.reasons)


def test_parse_json_recovers_from_chatty_models() -> None:
    assert _parse_json('```json\n{"summary": "x"}\n```')["summary"] == "x"
    assert _parse_json("no json here") == {}
    assert _parse_json('[1,2,3]') == {}
