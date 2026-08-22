"""Self-OSINT lifecycle: connect, sync, disconnect, delete.

Everything here is scoped to one authenticated user. There is no method that takes an
account id from a caller and acts on it: the account is always resolved *from* the
authenticated user, which is what makes IDOR structurally impossible rather than
merely checked for.

The audit reuses the ordinary pipeline — an ``Investigation`` owned by the user, the
``EntityExtractor``, the graph store, the correlation engine, the existing public-source
providers. Nothing about self-OSINT is a parallel data path; the only new ingredient is
where the Instagram half of the data comes from (the user's own authorised API call
instead of a public page).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.enums import InvestigationStatus, TargetType, TokenState
from app.models import (
    Entity,
    IdentityCandidate,
    Investigation,
    LinkedAccount,
    LinkedAccountRevocation,
    Note,
    OAuthState,
    Observation,
    Relationship,
    Target,
    User,
)
from app.models.base import as_utc, utcnow
from app.providers.registry import ProviderRegistry, get_registry
from app.providers.runner import ProviderRunner
from app.providers.types import Target as ProviderTarget
from app.security import crypto
from app.selfosint.instagram_oauth import (
    InstagramAuthRejected,
    InstagramNotConfigured,
    InstagramOAuthClient,
    InstagramOAuthError,
    TokenGrant,
)

logger = logging.getLogger(__name__)

PROVIDER = "instagram"
SELF_PROVIDER_NAME = "instagram_self"

#: Providers deliberately excluded from the external-discovery pass. The self provider
#: only works with a token, and re-running it here would be a no-op.
_EXCLUDED_FROM_DISCOVERY = {SELF_PROVIDER_NAME}


class SelfOsintError(Exception):
    """A user-facing failure. Messages here are safe to display; none quote a token."""


class NotConnected(SelfOsintError):
    pass


class SyncTooSoon(SelfOsintError):
    pass


@dataclass
class SyncOutcome:
    observations: int = 0
    entities: int = 0
    relationships: int = 0
    external_providers_run: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def hash_state(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class SelfOsintService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        oauth: InstagramOAuthClient | None = None,
        registry: ProviderRegistry | None = None,
        runner: ProviderRunner | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.oauth = oauth or InstagramOAuthClient(self.settings)
        self.registry = registry or get_registry()
        # Authorised personal data is never served from the shared provider cache.
        self.runner = runner or ProviderRunner(self.settings, use_cache=False)

    # -- lookup ----------------------------------------------------------------

    async def get_account(self, user: User) -> LinkedAccount | None:
        """The caller's own connection, or None. Never takes an account id."""
        return (
            await self.session.execute(
                select(LinkedAccount).where(
                    LinkedAccount.user_id == user.id, LinkedAccount.provider == PROVIDER
                )
            )
        ).scalar_one_or_none()

    async def require_account(self, user: User) -> LinkedAccount:
        account = await self.get_account(user)
        if account is None:
            raise NotConnected("No Instagram account is connected for this user.")
        return account

    # -- connect ---------------------------------------------------------------

    async def begin_connect(self, user: User) -> str:
        """Create a single-use state bound to ``user`` and return Meta's consent URL."""
        if not self.settings.instagram_oauth_configured:
            raise InstagramNotConfigured(
                "Instagram is not configured on this deployment. An administrator must "
                "set INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET."
            )
        # Clear this user's stale in-flight states so an abandoned attempt cannot be
        # completed later by whoever holds the old value.
        await self.session.execute(
            delete(OAuthState).where(
                OAuthState.user_id == user.id, OAuthState.provider == PROVIDER
            )
        )
        raw_state = secrets.token_urlsafe(32)
        self.session.add(
            OAuthState(
                id=uuid.uuid4(),
                user_id=user.id,
                provider=PROVIDER,
                state_hash=hash_state(raw_state),
                redirect_uri=self.settings.instagram_redirect_uri,
                expires_at=utcnow() + timedelta(seconds=self.settings.oauth_state_ttl_seconds),
            )
        )
        await self.session.flush()
        return self.oauth.authorize_url(state=raw_state)

    async def complete_connect(self, user: User, *, code: str, state: str) -> LinkedAccount:
        """Validate the state, exchange the code, and store the credential encrypted."""
        record = await self._consume_state(user, state)
        if record is None:
            # One message for every failure mode (unknown / expired / spent / wrong
            # user), so a caller cannot probe which states exist.
            raise SelfOsintError(
                "This authorisation request is not valid. It may have expired or already "
                "been used. Start the connection again."
            )

        grant = await self.oauth.exchange_code(code)
        logger.info(
            "instagram connect completed for user %s: %s", user.id, grant.redacted()
        )
        return await self._store_grant(user, grant)

    async def _consume_state(self, user: User, state: str) -> OAuthState | None:
        if not state:
            return None
        record = (
            await self.session.execute(
                select(OAuthState).where(OAuthState.state_hash == hash_state(state))
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        # The state must belong to the *authenticated* caller: a state leaked to an
        # attacker is useless without that user's session.
        if record.user_id != user.id or record.provider != PROVIDER:
            logger.warning("oauth state presented by a user it was not issued to")
            return None
        if record.is_spent or record.is_expired():
            return None
        record.used_at = utcnow()
        await self.session.flush()
        return record

    async def _store_grant(self, user: User, grant: TokenGrant) -> LinkedAccount:
        account = await self.get_account(user)
        if account is None:
            account = LinkedAccount(
                id=uuid.uuid4(), user_id=user.id, provider=PROVIDER, sync_meta={}
            )
            self.session.add(account)

        account.provider_account_id = grant.account_id
        account.encrypted_token = crypto.encrypt(
            grant.token.reveal(),
            aad=crypto.account_aad(user.id, PROVIDER),
            settings=self.settings,
        )
        account.token_state = TokenState.ACTIVE
        account.token_state_detail = ""
        account.expires_at = grant.expires_at
        account.scopes = list(grant.scopes)
        await self.session.flush()

        if account.investigation_id is None:
            account.investigation_id = (await self._create_investigation(user)).id
            await self.session.flush()
        return account

    async def _create_investigation(self, user: User) -> Investigation:
        investigation = Investigation(
            id=uuid.uuid4(),
            name="Self-audit: Instagram",
            description=(
                "Privacy self-audit of your own Instagram account, plus what public "
                "sources reveal about the identifiers it exposes."
            ),
            owner_id=user.id,
            tags=["self-osint", "instagram"],
            status=InvestigationStatus.DRAFT,
            config={"self_osint": True, "provider": PROVIDER},
        )
        self.session.add(investigation)
        await self.session.flush()
        return investigation

    # -- token -----------------------------------------------------------------

    def _decrypt(self, user: User, account: LinkedAccount) -> crypto.Secret:
        return crypto.decrypt(
            account.encrypted_token,
            aad=crypto.account_aad(user.id, PROVIDER),
            settings=self.settings,
        )

    async def _mark_token(
        self, account: LinkedAccount, state: TokenState, detail: str
    ) -> None:
        """Record a lifecycle transition, committing immediately.

        Committing here rather than flushing is deliberate: every caller that marks a
        token as unusable then raises, and the route turns that into an HTTP error
        *without* committing. A flush would be rolled back with the request, so the user
        would keep seeing "active" and would never be told to reconnect.
        """
        account.token_state = str(state)
        account.token_state_detail = detail[:512]
        await self.session.commit()

    # -- sync ------------------------------------------------------------------

    async def sync(self, user: User, *, force: bool = False) -> SyncOutcome:
        """Refresh the audit: read the authorised account, then look outward."""
        account = await self.require_account(user)
        if not force:
            self._enforce_cooldown(account)

        try:
            token = self._decrypt(user, account)
        except crypto.DecryptionError:
            await self._mark_token(
                account,
                TokenState.INVALID,
                "The stored credential could not be decrypted. Reconnect your account.",
            )
            raise SelfOsintError(
                "The stored credential could not be read. Please reconnect your account."
            ) from None

        expires_at = as_utc(account.expires_at)
        if expires_at is not None and expires_at <= datetime.now(tz=UTC):
            await self._mark_token(
                account, TokenState.EXPIRED, "The access token has expired. Reconnect."
            )
            raise SelfOsintError("Your Instagram authorisation has expired. Reconnect it.")

        try:
            profile = await self.oauth.verify(token)
        except InstagramAuthRejected as exc:
            await self._mark_token(account, TokenState.REAUTHORIZATION_REQUIRED, str(exc))
            raise SelfOsintError(
                "Instagram no longer accepts this authorisation. Reconnect your account."
            ) from exc
        except InstagramOAuthError as exc:
            raise SelfOsintError(f"Could not reach Instagram right now: {exc}") from exc

        handle = str(profile.get("username") or account.username or "")
        if not handle:
            raise SelfOsintError("Instagram did not return a username for this account.")
        account.username = handle
        account.account_type = str(profile.get("account_type") or "") or None
        if account.token_state != TokenState.ACTIVE:
            await self._mark_token(account, TokenState.ACTIVE, "")

        investigation_id = account.investigation_id
        if investigation_id is None:
            investigation_id = (await self._create_investigation(user)).id
            account.investigation_id = investigation_id
            await self.session.flush()

        outcome = await self._ingest_authorized(account, investigation_id, handle, token)
        await self._discover_external(user, investigation_id, handle, outcome)

        account.last_synced_at = utcnow()
        account.sync_meta = {
            "observations": outcome.observations,
            "entities": outcome.entities,
            "external_providers_run": outcome.external_providers_run,
            "errors": outcome.errors[:10],
            "handle": handle,
        }
        await self.session.flush()
        return outcome

    def _enforce_cooldown(self, account: LinkedAccount) -> None:
        last_synced_at = as_utc(account.last_synced_at)
        if last_synced_at is None:
            return
        elapsed = (utcnow() - last_synced_at).total_seconds()
        remaining = self.settings.self_osint_sync_cooldown_seconds - elapsed
        if remaining > 0:
            raise SyncTooSoon(
                f"Synchronised recently. Try again in {int(remaining) + 1} seconds. "
                "Instagram is not polled continuously, by design."
            )

    async def _ingest_authorized(
        self,
        account: LinkedAccount,
        investigation_id: uuid.UUID,
        handle: str,
        token: crypto.Secret,
    ) -> SyncOutcome:
        """Run the self provider with this user's own credential and store the result."""
        from app.evidence.extractor import EntityExtractor

        outcome = SyncOutcome()
        entry = self.registry.get(SELF_PROVIDER_NAME)
        if entry is None:
            outcome.errors.append(
                f"The {SELF_PROVIDER_NAME} provider is not installed on this deployment."
            )
            return outcome

        target = ProviderTarget(
            type=TargetType.USERNAME, value=handle, normalized=handle.lower(), depth=0
        )
        result = await self.runner.run(
            entry,
            target,
            # Per-call only: never written to the source registry, never cached.
            config={
                "access_token": token.reveal(),
                "graph_base": self.settings.instagram_graph_base,
                "scopes": list(account.scopes),
            },
        )
        if result.error:
            outcome.errors.append(f"instagram_self: {result.error}")
            return outcome

        extractor = EntityExtractor(self.session, investigation_id)
        extraction = await extractor.ingest(target, result.observations)
        outcome.observations = len(result.observations)
        outcome.entities = len(extraction.new_entities)
        outcome.relationships = len(extraction.new_relationships)

        # The identifiers the authorised account exposes become the seeds for the
        # outward-looking pass. This is the only place the two halves meet, and it is
        # deliberately one-directional: public data never flows back into the token.
        await self._seed_targets(investigation_id, handle, extraction.derived_targets)
        await self.session.flush()
        return outcome

    async def _seed_targets(
        self,
        investigation_id: uuid.UUID,
        handle: str,
        derived: list[ProviderTarget],
    ) -> None:
        from app.schemas.investigation import TargetIn
        from app.services.investigation import InvestigationService

        seeds = [TargetIn(value=handle, type=TargetType.USERNAME)]
        for item in derived:
            # Only identifier kinds a person can legitimately be looked up by. Post URLs
            # and hashtags are not identity seeds.
            if item.type in (TargetType.USERNAME, TargetType.EMAIL, TargetType.DOMAIN):
                seeds.append(TargetIn(value=item.value, type=item.type))
        await InvestigationService(self.session).add_targets(investigation_id, seeds[:25])

    async def _discover_external(
        self, user: User, investigation_id: uuid.UUID, handle: str, outcome: SyncOutcome
    ) -> None:
        """Run the ordinary public-source providers over the exposed identifiers.

        This is the "how exposed am I" half: exactly the search an outsider could run,
        using only public sources and only the identifiers the account already publishes.
        """
        from app.core.db import get_sessionmaker
        from app.jobs.orchestrator import Orchestrator, ScanRequest

        names = [
            entry.name
            for entry in self.registry.all()
            if entry.enabled and entry.name not in _EXCLUDED_FROM_DISCOVERY
        ]
        if not names:
            return
        # Commit the seeds so the orchestrator's own session can see them.
        await self.session.commit()
        try:
            report = await Orchestrator(get_sessionmaker(), registry=self.registry).run(
                ScanRequest(
                    investigation_id=investigation_id, providers=names, recursive=False
                )
            )
        except Exception as exc:  # a discovery failure must not lose the authorised data
            logger.warning("self-osint external discovery failed: %s", exc)
            outcome.errors.append(f"external discovery: {type(exc).__name__}")
            return
        outcome.external_providers_run = report.jobs_run
        outcome.observations += report.observations
        outcome.entities += report.entities_created
        outcome.relationships += report.relationships_created
        outcome.errors.extend(report.errors[:10])

    # -- disconnect and delete -------------------------------------------------

    async def disconnect(self, user: User) -> LinkedAccountRevocation:
        """Stop using the credential and destroy it, keeping a minimal audit tombstone.

        Instagram Login has no server-side revoke endpoint, so ``remote_revocation`` is
        recorded as False and the caller is given the URL where the user can withdraw
        access on Meta's side. Locally the credential is gone either way.
        """
        account = await self.require_account(user)
        tombstone = LinkedAccountRevocation(
            id=uuid.uuid4(),
            user_id=user.id,
            provider=PROVIDER,
            account_ref=(account.provider_account_id or "")[:12],
            reason="disconnected",
            remote_revocation=False,
            data_deleted=False,
        )
        self.session.add(tombstone)

        account.encrypted_token = None
        account.token_state = str(TokenState.REVOKED)
        account.token_state_detail = "Disconnected by the account owner."  # noqa: S105
        account.expires_at = None
        account.scopes = []
        await self.session.flush()
        return tombstone

    async def delete_data(self, user: User) -> dict[str, int]:
        """Erase everything collected for this user's self-audit.

        Removes the credential, the audit investigation and every entity, observation,
        relationship and candidate under it. Audit-log rows survive deliberately: they
        record that an action happened and by whom, not what was found.
        """
        account = await self.get_account(user)
        investigation_id = account.investigation_id if account else None
        counts = {"observations": 0, "entities": 0, "relationships": 0, "investigations": 0}

        if investigation_id is not None:
            investigation = await self.session.get(Investigation, investigation_id)
            # Ownership re-checked here rather than assumed: this is a destructive path.
            if investigation is not None and investigation.owner_id == user.id:
                counts["observations"] = await self._count(Observation, investigation_id)
                counts["entities"] = await self._count(Entity, investigation_id)
                counts["relationships"] = await self._count(Relationship, investigation_id)
                for model in (
                    Observation,
                    IdentityCandidate,
                    Relationship,
                    Note,
                    Entity,
                    Target,
                ):
                    await self.session.execute(
                        delete(model).where(model.investigation_id == investigation_id)
                    )
                await self.session.delete(investigation)
                counts["investigations"] = 1

        if account is not None:
            self.session.add(
                LinkedAccountRevocation(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    provider=PROVIDER,
                    account_ref=(account.provider_account_id or "")[:12],
                    reason="data_deleted",
                    remote_revocation=False,
                    data_deleted=True,
                )
            )
            await self.session.delete(account)
        await self.session.flush()
        return counts

    async def _count(self, model: Any, investigation_id: uuid.UUID) -> int:
        from sqlalchemy import func

        return int(
            (
                await self.session.execute(
                    select(func.count()).select_from(model).where(
                        model.investigation_id == investigation_id
                    )
                )
            ).scalar_one()
        )
