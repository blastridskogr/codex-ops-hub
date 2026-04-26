"""Common response envelope."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from .errors import ErrorItem, WarningItem

DataT = TypeVar("DataT")


class ResponseEnvelope(BaseModel, Generic[DataT]):
    """Uniform MCP response envelope."""

    ok: bool
    tool: str
    data: DataT | None = None
    warnings: list[WarningItem] = Field(default_factory=list)
    errors: list[ErrorItem] = Field(default_factory=list)

    @classmethod
    def success(
        cls,
        *,
        tool: str,
        data: DataT | None = None,
        warnings: list[WarningItem] | None = None,
    ) -> "ResponseEnvelope[DataT]":
        return cls(ok=True, tool=tool, data=data, warnings=warnings or [], errors=[])

    @classmethod
    def failure(
        cls,
        *,
        tool: str,
        errors: list[ErrorItem],
        warnings: list[WarningItem] | None = None,
    ) -> "ResponseEnvelope[DataT]":
        return cls(ok=False, tool=tool, data=None, warnings=warnings or [], errors=errors)
