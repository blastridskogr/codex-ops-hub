"""JSON-backed workspace state store."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.schemas.state import IdempotencyState, TaskState

_SUPPORTED_STATE_SCHEMA = 1


class StateStoreError(RuntimeError):
    """Raised when state cannot be loaded or persisted safely."""


class WorkspaceStateStore:
    """Per-workspace state files."""

    def __init__(self, root: Path, workspace_id: str) -> None:
        self.root = root
        self.workspace_id = workspace_id

    @property
    def workspace_dir(self) -> Path:
        return self.root / self.workspace_id

    @property
    def state_path(self) -> Path:
        return self.workspace_dir / "state.json"

    @property
    def violations_path(self) -> Path:
        return self.workspace_dir / "violations.json"

    @property
    def idempotency_path(self) -> Path:
        return self.workspace_dir / "idempotency.json"

    @property
    def tasks_dir(self) -> Path:
        return self.workspace_dir / "tasks"

    @property
    def archive_dir(self) -> Path:
        return self.workspace_dir / "archive"

    def ensure_layout(self) -> None:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> TaskState:
        if not self.state_path.exists():
            raise StateStoreError("STATE_NOT_FOUND")
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"STATE_INVALID: {exc}") from exc

        version = payload.get("state_schema_version")
        if version is None:
            raise StateStoreError("STATE_SCHEMA_MISSING")
        if version != _SUPPORTED_STATE_SCHEMA:
            raise StateStoreError("STATE_SCHEMA_UNSUPPORTED")
        try:
            return TaskState.model_validate(payload)
        except ValidationError as exc:
            raise StateStoreError(f"STATE_VALIDATION_FAILED: {exc}") from exc

    def save_state(self, state: TaskState) -> None:
        self.ensure_layout()
        atomic_write_text(self.state_path, state.model_dump_json(indent=2))

    def load_idempotency(self) -> IdempotencyState:
        if not self.idempotency_path.exists():
            return IdempotencyState()
        try:
            payload = json.loads(self.idempotency_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"IDEMPOTENCY_INVALID: {exc}") from exc
        return IdempotencyState.model_validate(payload)

    def save_idempotency(self, state: IdempotencyState) -> None:
        self.ensure_layout()
        atomic_write_text(self.idempotency_path, state.model_dump_json(indent=2))
