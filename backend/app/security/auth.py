"""Password hashing (Argon2id) and JWT access/refresh tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
ALGORITHM = "HS256"
TokenKind = Literal["access", "refresh"]


class TokenError(ValueError):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


@dataclass(frozen=True)
class TokenClaims:
    subject: uuid.UUID
    role: str
    kind: TokenKind
    expires_at: datetime
    jti: str


def create_token(user_id: uuid.UUID, role: str, kind: TokenKind = "access") -> str:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if kind == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "kind": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, expected_kind: TokenKind | None = None) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "kind"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc

    kind = payload.get("kind")
    if expected_kind is not None and kind != expected_kind:
        raise TokenError(f"expected a {expected_kind} token")
    try:
        subject = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenError("invalid subject") from exc
    return TokenClaims(
        subject=subject,
        role=str(payload.get("role", "viewer")),
        kind=kind,  # type: ignore[arg-type]
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        jti=str(payload.get("jti", "")),
    )
