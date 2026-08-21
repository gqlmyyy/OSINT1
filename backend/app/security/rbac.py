"""Role hierarchy and the permission matrix."""

from __future__ import annotations

from app.core.enums import Role

#: Higher number implies every permission of the lower ones.
_RANK: dict[str, int] = {Role.VIEWER: 10, Role.ANALYST: 20, Role.ADMIN: 30}

PERMISSIONS: dict[str, str] = {
    "investigation:read": Role.VIEWER,
    "investigation:write": Role.ANALYST,
    "investigation:delete": Role.ADMIN,
    "scan:run": Role.ANALYST,
    "entity:expand": Role.ANALYST,
    "export:create": Role.VIEWER,
    "source:read": Role.VIEWER,
    "source:write": Role.ADMIN,
    "user:manage": Role.ADMIN,
    "ai:use": Role.ANALYST,
}


def rank(role: str) -> int:
    return _RANK.get(role, 0)


def has_role(actual: str, required: str) -> bool:
    return rank(actual) >= rank(required)


def can(role: str, permission: str) -> bool:
    required = PERMISSIONS.get(permission)
    if required is None:
        raise KeyError(f"unknown permission: {permission}")
    return has_role(role, required)
