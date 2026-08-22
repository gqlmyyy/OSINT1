"""Self-OSINT: a platform account a user has authorised us to read *their own* data from.

Two tables, both scoped to exactly one user:

``LinkedAccount``
    One row per (user, provider). Holds the encrypted OAuth credential and its lifecycle
    state. The token column stores a sealed envelope (see ``app.security.crypto``), never
    plaintext, and the envelope is bound to this row's owner so it cannot be moved to
    another user's row.

``OAuthState``
    A single-use, expiring CSRF token bound to the user who started the flow. The raw
    value is never stored — only its SHA-256 — so a database read cannot be replayed as a
    callback. Rows are consumed on first use.

Neither table is shared or global: there is deliberately no notion of a platform-wide
Instagram connection here, because one shared token would make every user's audit read
somebody else's account.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, JSONVariant
from app.core.enums import TokenState
from app.models.base import Timestamped, UUIDPk, as_utc, utcnow


class LinkedAccount(UUIDPk, Timestamped, Base):
    """A user's own authorised account on an external platform."""

    __tablename__ = "linked_accounts"
    __table_args__ = (
        # One connection per provider per user. Re-connecting updates in place, so a
        # user cannot accumulate stale credentials for the same platform.
        UniqueConstraint("user_id", "provider", name="uq_linked_account_user_provider"),
        Index("ix_linked_account_user", "user_id", "provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The platform's own id for the account. Recorded from the *token exchange*, never
    #: accepted from the client: a caller cannot assert ownership of an arbitrary account.
    provider_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: AES-256-GCM envelope. Never logged, never serialised to any API response.
    encrypted_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    token_state: Mapped[str] = mapped_column(
        String(32), default=TokenState.ACTIVE, nullable=False, index=True
    )
    #: Human-readable reason the token is not ACTIVE. Must never quote token contents.
    token_state_detail: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)

    #: The self-audit investigation this connection feeds. Reuses the ordinary
    #: investigation/entity/observation pipeline rather than a parallel store.
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Non-sensitive counters and the last sync's outcome, for the UI.
    sync_meta: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)

    @property
    def is_usable(self) -> bool:
        return self.token_state == TokenState.ACTIVE and self.encrypted_token is not None


class OAuthState(UUIDPk, Timestamped, Base):
    """Single-use CSRF state for an in-flight authorisation, bound to one user."""

    __tablename__ = "oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_oauth_state_hash"),
        Index("ix_oauth_state_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: SHA-256 of the value handed to the browser. Storing the hash means a leaked
    #: database row cannot be replayed as a valid callback.
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_spent(self) -> bool:
        return self.used_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        expires_at = as_utc(self.expires_at)
        return expires_at is None or (now or utcnow()) >= expires_at


class LinkedAccountRevocation(UUIDPk, Timestamped, Base):
    """Minimal tombstone kept after a disconnect.

    Disconnect removes the credential and stops synchronisation; this row is what
    survives, so an operator can still answer "was this account ever connected, by whom,
    and when was it removed" without retaining the connection itself. It holds no token
    and no profile content.
    """

    __tablename__ = "linked_account_revocations"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Truncated/opaque reference, not the full platform id, to keep this minimal.
    account_ref: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="disconnected", nullable=False)
    remote_revocation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
