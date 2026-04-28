"""Error and violation models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ErrorSeverity = Literal["error", "warning", "info"]
ErrorCode = Literal[
    "ACTIVE_TASK_EXISTS",
    "ADS_PATH_REJECTED",
    "CONFIG_SCHEMA_UNSUPPORTED",
    "DELETE_NOT_ALLOWED",
    "EXTENDED_PATH_REJECTED",
    "FINGERPRINT_COMPUTE_FAILED",
    "FORBIDDEN_PATTERN",
    "GIT_COMMAND_FAILED",
    "GIT_NOT_REPO",
    "HERMES_DIRECT_MODE_NOT_IMPLEMENTED",
    "HERMES_SYNC_REQUIRES_DIRECT_MODE",
    "HERMES_UNAVAILABLE",
    "IDEMPOTENCY_PAYLOAD_MISMATCH",
    "JUNCTION_TARGET_OUTSIDE_REPO",
    "JUNCTION_WRITE_FORBIDDEN",
    "LOCK_HEARTBEAT_STALE",
    "LOCK_SCHEMA_INVALID",
    "LOCK_SCHEMA_MISSING",
    "LOCK_SCHEMA_UNSUPPORTED",
    "LOCK_TIMESTAMP_INVALID",
    "MANAGED_FILE_NOT_DECLARED",
    "MEMORY_PREFLIGHT_HAS_BLOCKERS",
    "MEMORY_PREFLIGHT_LOOKUP_NOT_RUN",
    "MEMORY_PREFLIGHT_PROJECT_MISMATCH",
    "MEMORY_PREFLIGHT_REPO_MISMATCH",
    "MEMORY_PREFLIGHT_REQUIRED",
    "OBSIDIAN_DISABLED",
    "OUTSIDE_ALLOWED_FILES",
    "PATH_OUTSIDE_REPO",
    "PENDING_VERSION_NOT_SYNCED",
    "RAW_DIFF_REJECTED",
    "RAW_LOG_REJECTED",
    "RENAME_NOT_ALLOWED",
    "RESERVED_DEVICE_NAME",
    "SECRET_DETECTED",
    "STRICT_APPLY_PATCH_DISABLED",
    "STRICT_MANAGED_WRITE_DISABLED",
    "STRICT_MODE_NOT_ENABLED",
    "STATE_LOCK_TIMEOUT",
    "STATE_NOT_FOUND",
    "STATE_SCHEMA_MISSING",
    "STATE_SCHEMA_UNSUPPORTED",
    "SUBMODULE_CHANGE_NOT_ALLOWED",
    "SYMLINK_TARGET_OUTSIDE_REPO",
    "SYMLINK_WRITE_FORBIDDEN",
    "TASK_ID_CONFLICT",
    "TRAILING_DOT_PATH_REJECTED",
    "TRAILING_SPACE_PATH_REJECTED",
    "UNC_PATH_REJECTED",
    "UNSAFE_WINDOWS_PATH",
    "VERSION_PATH_MISMATCH",
    "WIKI_NOTE_WRITE_FAILED",
    "WIKI_SCHEMA_UNSUPPORTED",
    "WRITE_BEFORE_PLAN",
    "NO_PENDING_VERSION",
    "PATCH_OUTSIDE_ALLOWED_FILES",
    "PATCH_PRECONDITION_FAILED",
    "PATCH_TARGET_MISSING",
    "MANAGED_FILE_WRITE_REQUIRES_VERSION_FLOW",
    "WORKTREE_CHANGED_DURING_FINISH",
]


class ErrorItem(BaseModel):
    """Top-level tool execution failure."""

    code: ErrorCode
    message: str
    path: str | None = None
    severity: ErrorSeverity = "error"
    details: dict[str, str] = Field(default_factory=dict)


class WarningItem(BaseModel):
    """Non-fatal warning returned by a tool."""

    code: str
    message: str


class ViolationItem(BaseModel):
    """Policy violation discovered by a successful check."""

    type: str
    message: str
    severity: ErrorSeverity = "error"
    path: str | None = None
