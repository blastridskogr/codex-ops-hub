"""Archive helpers for one-active-task-per-workspace."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.schemas.state import TaskState

from .state_store import WorkspaceStateStore


class ArchiveError(RuntimeError):
    """Raised when active state archival fails."""


def archive_active_state(
    store: WorkspaceStateStore,
    state: TaskState,
    *,
    reason: str,
) -> Path:
    """Copy current workspace state into archive before replacing active state."""

    store.ensure_layout()
    archive_root = store.archive_dir
    target = archive_root / state.task_id
    temp_target = archive_root / f"{state.task_id}.tmp"

    if target.exists() or temp_target.exists():
        raise ArchiveError(f"Archive target already exists for task {state.task_id}.")

    temp_target.mkdir(parents=True, exist_ok=False)
    try:
        for source in (store.state_path, store.violations_path, store.idempotency_path):
            if source.exists():
                shutil.copy2(source, temp_target / source.name)
        if store.tasks_dir.exists():
            shutil.copytree(store.tasks_dir, temp_target / "tasks")
        metadata = {
            "archive_schema_version": 1,
            "archived_at": now_local_iso(),
            "reason": reason,
            "previous_phase": state.phase,
            "workspace_id": state.workspace_id,
            "task_id": state.task_id,
        }
        atomic_write_text(temp_target / "archive_metadata.json", json.dumps(metadata, indent=2))
        temp_target.rename(target)
    except Exception as exc:
        shutil.rmtree(temp_target, ignore_errors=True)
        raise ArchiveError(f"Failed to archive task {state.task_id}: {exc}") from exc
    return target
