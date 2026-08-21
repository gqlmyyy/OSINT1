from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.core.enums import Role
from app.schemas.common import ORMModel, StrictModel

MIN_PASSWORD_LENGTH = 12


class RegisterRequest(StrictModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    role: Role | None = None

    @field_validator("password")
    @classmethod
    def _not_trivial(cls, value: str) -> str:
        if value.lower() in {"password1234", "changeme1234", "graphintel1"}:
            raise ValueError("password is too common")
        if len(set(value)) < 5:
            raise ValueError("password is not varied enough")
        return value


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=8)


class TokenPair(StrictModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    username: str
    role: str
    is_active: bool
    created_at: datetime
