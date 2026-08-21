"""Live investigation feed (spec 13)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.enums import Role
from app.jobs.events import channel_for, get_event_bus
from app.models import Investigation, User
from app.security.auth import TokenError, decode_token
from app.security.rbac import has_role

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403


@router.websocket("/ws/investigations/{investigation_id}")
async def investigation_feed(
    websocket: WebSocket,
    investigation_id: uuid.UUID,
    token: str = Query(default=""),
) -> None:
    """Token is passed as a query parameter because browsers cannot set WS headers."""
    try:
        claims = decode_token(token, "access")
    except TokenError:
        await websocket.close(code=WS_UNAUTHORIZED, reason="invalid token")
        return

    async with get_sessionmaker()() as session:
        if not await _authorized(session, claims.subject, investigation_id):
            await websocket.close(code=WS_FORBIDDEN, reason="not authorized")
            return

    await websocket.accept()
    bus = get_event_bus()
    channel = channel_for(investigation_id)

    async def pump() -> None:
        async for event in bus.subscribe(channel):
            await websocket.send_json(event)

    task = asyncio.create_task(pump())
    try:
        await websocket.send_json({"event": "connected", "investigation_id": str(investigation_id)})
        while True:
            # Client messages are keepalives only; nothing here trusts their content.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("websocket closed unexpectedly", exc_info=True)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _authorized(
    session: AsyncSession, user_id: uuid.UUID, investigation_id: uuid.UUID
) -> bool:
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return False
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        return False
    return investigation.owner_id == user.id or has_role(user.role, Role.ADMIN)
