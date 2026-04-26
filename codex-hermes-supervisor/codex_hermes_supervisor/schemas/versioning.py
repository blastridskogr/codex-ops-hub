"""Versioning-related models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VersioningMode = Literal["side_by_side", "versions_dir"]


class ManagedFileDeclaration(BaseModel):
    active_path: str
    reason: str
    versioning_mode: VersioningMode = "side_by_side"


class ManagedFileState(BaseModel):
    active_path: str
    versions: list[str] = Field(default_factory=list)
    pending_version: str | None = None
    synced: bool = False
    active_hash: str | None = None
    version_hash: str | None = None
    versioning_mode: VersioningMode = "side_by_side"


class VersionPrepareData(BaseModel):
    active_path: str
    version_path: str
    previous_version_path: str | None = None
    pending: bool = True
    sync_required: bool = True
    instruction: str


class VersionSyncData(BaseModel):
    active_path: str
    version_path: str
    active_hash: str
    version_hash: str
    synced: bool = True
    pending: bool = False
