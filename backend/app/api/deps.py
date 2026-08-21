"""Shared FastAPI dependencies: auth, RBAC, ownership, pagination."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.enums import Role
from app.models import Investigation, User
from app.security.auth import TokenError, decode_token
from app.security.rbac import PERMISSIONS, has_role

bearer = HTTPBearer(auto_error=False)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_token(credentials.credentials, "access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission: str) -> object:
    """Route dependency enforcing one entry of the permission matrix."""
    required = PERMISSIONS[permission]

    async def _dependency(user: CurrentUser) -> User:
        if not has_role(user.role, required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{user.role}' lacks permission '{permission}'",
            )
        return user

    return Depends(_dependency)


def require_role(role: Role) -> object:
    async def _dependency(user: CurrentUser) -> User:
        if not has_role(user.role, role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"requires role '{role}'"
            )
        return user

    return Depends(_dependency)


async def get_investigation(
    session: SessionDep,
    user: CurrentUser,
    investigation_id: Annotated[uuid.UUID, Path()],
) -> Investigation:
    """Fetch an investigation the caller is allowed to see."""
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="investigation not found")
    if investigation.owner_id != user.id and not has_role(user.role, Role.ADMIN):
        # 404 rather than 403: existence of another analyst's case is itself information.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="investigation not found")
    return investigation


InvestigationDep = Annotated[Investigation, Depends(get_investigation)]


class PageParams:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


PageDep = Annotated[PageParams, Depends(PageParams)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None
