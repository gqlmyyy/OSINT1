from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep, client_ip
from app.core.config import get_settings
from app.core.enums import Role
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from app.security import audit
from app.security.auth import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.ratelimit import TokenBucket

router = APIRouter(prefix="/auth", tags=["auth"])

#: Login throttle. Deliberately per-username so one attacker cannot lock out everyone.
_login_bucket = TokenBucket(rate_per_minute=10, burst=10)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: SessionDep, request: Request) -> User:
    settings = get_settings()
    user_count = int((await session.execute(select(func.count(User.id)))).scalar_one())
    if user_count > 0 and not settings.allow_registration:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="registration is closed")

    clash = (
        await session.execute(
            select(User).where(
                or_(User.email == payload.email.lower(), User.username == payload.username.lower())
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email or username already registered"
        )

    # The first account is the administrator; everyone after defaults to analyst.
    role = payload.role or (Role.ADMIN if user_count == 0 else Role.ANALYST)
    user = User(
        email=payload.email.lower(),
        username=payload.username.lower(),
        password_hash=hash_password(payload.password),
        role=str(role),
    )
    session.add(user)
    await session.flush()
    await audit.record(
        session, action="auth.register", actor_id=user.id, target=user.username, ip=client_ip(request)
    )
    return user


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, session: SessionDep, request: Request) -> TokenPair:
    identifier = payload.username.strip().lower()
    if not await _login_bucket.try_acquire(identifier):
        retry = int(await _login_bucket.retry_after(identifier)) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
            headers={"Retry-After": str(retry)},
        )

    user = (
        await session.execute(
            select(User).where(or_(User.username == identifier, User.email == identifier))
        )
    ).scalar_one_or_none()

    # Uniform failure: never reveal whether the account exists.
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        await audit.record(
            session, action="auth.login_failed", target=identifier, ip=client_ip(request)
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    await audit.record(
        session, action="auth.login", actor_id=user.id, target=user.username, ip=client_ip(request)
    )
    return _tokens(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token, "refresh")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = await session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return _tokens(user)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


def _tokens(user: User) -> TokenPair:
    settings = get_settings()
    return TokenPair(
        access_token=create_token(user.id, user.role, "access"),
        refresh_token=create_token(user.id, user.role, "refresh"),
        expires_in=settings.access_token_ttl_minutes * 60,
    )
