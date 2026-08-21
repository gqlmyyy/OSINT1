from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class StrictModel(BaseModel):
    """Every request body rejects unknown fields (spec 24: strict validation)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Pagination(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class Message(BaseModel):
    detail: str
