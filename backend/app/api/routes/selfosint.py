"""Self-OSINT endpoints: connect, inspect, sync, disconnect, delete.

Every route here resolves the account **from the authenticated caller**. None of them
accepts an account id, an investigation id, or a user id from the client, so there is no
identifier to tamper with — IDOR is prevented by the shape of the API rather than by a
check that could be forgotten on a new route.

The one unauthenticated route is ``GET /callback``, which exists only because Meta
redirects a browser there. It validates nothing, exchanges nothing, and stores nothing:
it hands the opaque parameters back to the single-page app, which completes the flow
against the authenticated ``POST /callback``. That keeps token exchange behind the same
bearer-token authentication as everything else.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, SessionDep, client_ip
from app.core.config import get_settings
from app.models import LinkedAccount
from app.schemas.selfosint import (
    CallbackIn,
    ConnectStart,
    CorrelationOut,
    DeleteOut,
    DisconnectOut,
    FindingOut,
    LinkedAccountOut,
    ReportOut,
    ScoreOut,
    SyncOut,
)
from app.security import audit
from app.selfosint.instagram_capabilities import resolve_all
from app.selfosint.instagram_oauth import (
    InstagramNotConfigured,
    InstagramOAuthError,
    revocation_instructions,
)
from app.selfosint.report import ExposureReportBuilder
from app.selfosint.service import NotConnected, SelfOsintError, SelfOsintService, SyncTooSoon

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/self/instagram", tags=["self-osint"])


def _account_out(account: LinkedAccount | None, *, configured: bool) -> LinkedAccountOut:
    """Build the response by hand. No ORM row is ever serialised wholesale here."""
    if account is None:
        return LinkedAccountOut(
            connected=False,
            oauth_configured=configured,
            capabilities=resolve_all([]),  # type: ignore[arg-type]
            revocation_instructions=revocation_instructions(),
        )
    return LinkedAccountOut(
        connected=account.encrypted_token is not None,
        provider=account.provider,
        username=account.username,
        account_type=account.account_type,
        provider_account_id=account.provider_account_id,
        token_state=account.token_state,
        token_state_detail=account.token_state_detail,
        expires_at=account.expires_at,
        scopes=list(account.scopes),
        last_synced_at=account.last_synced_at,
        investigation_id=str(account.investigation_id) if account.investigation_id else None,
        sync_meta=dict(account.sync_meta),
        capabilities=resolve_all(list(account.scopes)),  # type: ignore[arg-type]
        oauth_configured=configured,
        revocation_instructions=revocation_instructions(),
    )


@router.post("/connect", response_model=ConnectStart)
async def connect(
    session: SessionDep, user: CurrentUser, request: Request
) -> ConnectStart:
    """Start the official Meta OAuth flow for *the calling user's own* account."""
    settings = get_settings()
    service = SelfOsintService(session)
    try:
        url = await service.begin_connect(user)
    except InstagramNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    await audit.record(
        session,
        action="self_osint.connect_started",
        actor_id=user.id,
        target="instagram",
        ip=client_ip(request),
        meta={"scopes": list(settings.instagram_scopes)},
    )
    await session.commit()
    return ConnectStart(authorize_url=url, scopes=list(settings.instagram_scopes))


@router.get("/callback", include_in_schema=False)
async def callback_redirect(
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Meta's browser redirect target.

    Deliberately inert: no authentication is possible on a bare browser redirect with
    bearer-token auth, so this endpoint refuses to make any decision. It forwards the
    opaque parameters to the SPA, which completes the flow authenticated.
    """
    settings = get_settings()
    params = {k: v for k, v in
              {"code": code, "state": state, "error": error,
               "error_description": error_description}.items() if v}
    origin = settings.cors_origins[0] if settings.cors_origins else ""
    return RedirectResponse(
        url=f"{origin.rstrip('/')}/self-osint/instagram?{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/callback", response_model=LinkedAccountOut)
async def complete_callback(
    payload: CallbackIn, session: SessionDep, user: CurrentUser, request: Request
) -> LinkedAccountOut:
    """Validate the state, exchange the code, store the credential encrypted."""
    settings = get_settings()
    service = SelfOsintService(session)
    try:
        account = await service.complete_connect(
            user, code=payload.code, state=payload.state
        )
    except InstagramNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except SelfOsintError as exc:
        await audit.record(
            session,
            action="self_osint.connect_rejected",
            actor_id=user.id,
            target="instagram",
            ip=client_ip(request),
            meta={"reason": "state_validation_failed"},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except InstagramOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    await audit.record(
        session,
        action="self_osint.connected",
        actor_id=user.id,
        target=f"instagram:{account.provider_account_id}",
        ip=client_ip(request),
        # Scopes and account id only. There is no code path that could put a token here.
        meta={"scopes": list(account.scopes), "username": account.username},
    )
    await session.commit()
    return _account_out(account, configured=settings.instagram_oauth_configured)


@router.get("/account", response_model=LinkedAccountOut)
async def get_account(session: SessionDep, user: CurrentUser) -> LinkedAccountOut:
    """The caller's own connection status. Returns ``connected: false`` when absent."""
    settings = get_settings()
    account = await SelfOsintService(session).get_account(user)
    return _account_out(account, configured=settings.instagram_oauth_configured)


@router.post("/sync", response_model=SyncOut)
async def sync(
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    force: Annotated[bool, Query()] = False,
) -> SyncOut:
    """Re-read the authorised account and re-run public discovery over its identifiers."""
    service = SelfOsintService(session)
    try:
        outcome = await service.sync(user, force=force)
    except NotConnected as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SyncTooSoon as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except SelfOsintError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    account = await service.get_account(user)
    await audit.record(
        session,
        action="self_osint.synced",
        actor_id=user.id,
        target="instagram",
        ip=client_ip(request),
        meta={"observations": outcome.observations, "entities": outcome.entities},
    )
    await session.commit()
    return SyncOut(
        synced=True,
        observations=outcome.observations,
        entities=outcome.entities,
        relationships=outcome.relationships,
        external_providers_run=outcome.external_providers_run,
        errors=outcome.errors,
        last_synced_at=account.last_synced_at if account else None,
    )


@router.get("/report", response_model=ReportOut)
async def report(session: SessionDep, user: CurrentUser) -> Any:
    """The privacy exposure report for the caller's own connected account."""
    settings = get_settings()
    service = SelfOsintService(session)
    account = await service.get_account(user)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Instagram account is connected for this user.",
        )
    account_out = _account_out(account, configured=settings.instagram_oauth_configured)
    if account.investigation_id is None:
        return ReportOut(
            account=account_out,
            score=ScoreOut(
                overall=0,
                level="none",
                explanation="Nothing has been collected yet. Run a synchronisation first.",
            ),
            counts={},
            generated_at=datetime.now(tz=UTC),
        )
    built = await ExposureReportBuilder(session, account.investigation_id).build(
        handle=account.username
    )
    # Validate through the response models rather than passing dicts through: a builder
    # that drifts from the contract fails here instead of shipping a malformed report.
    return ReportOut(
        account=account_out,
        findings=[FindingOut(**item) for item in built["findings"]],
        correlations=[CorrelationOut(**item) for item in built["correlations"]],
        score=ScoreOut(**built["score"]),
        counts=built["counts"],
        generated_at=datetime.now(tz=UTC),
    )


@router.delete("/disconnect", response_model=DisconnectOut)
async def disconnect(
    session: SessionDep, user: CurrentUser, request: Request
) -> DisconnectOut:
    """Destroy the stored credential and stop synchronising. Collected data is kept."""
    service = SelfOsintService(session)
    try:
        tombstone = await service.disconnect(user)
    except NotConnected as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await audit.record(
        session,
        action="self_osint.disconnected",
        actor_id=user.id,
        target="instagram",
        ip=client_ip(request),
        meta={"remote_revocation": tombstone.remote_revocation},
    )
    await session.commit()
    return DisconnectOut(
        disconnected=True,
        remote_revocation=tombstone.remote_revocation,
        instructions=revocation_instructions(),
    )


@router.delete("/data", response_model=DeleteOut)
async def delete_data(
    session: SessionDep, user: CurrentUser, request: Request
) -> DeleteOut:
    """Erase the credential and everything collected for this user's self-audit."""
    removed = await SelfOsintService(session).delete_data(user)
    await audit.record(
        session,
        action="self_osint.data_deleted",
        actor_id=user.id,
        target="instagram",
        ip=client_ip(request),
        meta=removed,
    )
    await session.commit()
    return DeleteOut(deleted=True, removed=removed)
