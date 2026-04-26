"""Stale lock inspection and repair helpers."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.locks import WorkspaceLock
from codex_hermes_supervisor.core.paths import supervisor_state_root
from codex_hermes_supervisor.core.identity import normalize_windows_path


class LockRepairItem(BaseModel):
    path: str
    action: str
    reason: str


class LockRepairReport(BaseModel):
    checked: int = 0
    repaired: int = 0
    skipped: int = 0
    items: list[LockRepairItem] = Field(default_factory=list)


def repair_stale_locks(config: SupervisorConfig, *, force: bool = False) -> LockRepairReport:
    """Inspect workspace lock files and remove stale ones when safe."""

    report = LockRepairReport()
    state_root = config.state_root or supervisor_state_root()
    if not state_root.exists():
        return report

    current_host = socket.gethostname()
    for lock_path in state_root.rglob("lock.json"):
        report.checked += 1
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if force:
                lock_path.unlink(missing_ok=True)
                report.repaired += 1
                report.items.append(
                    LockRepairItem(
                        path=normalize_windows_path(lock_path),
                        action="removed",
                        reason=f"unreadable lock ({exc})",
                    )
                )
            else:
                report.skipped += 1
                report.items.append(
                    LockRepairItem(
                        path=normalize_windows_path(lock_path),
                        action="skipped",
                        reason=f"unreadable lock ({exc}); use --force to clear",
                    )
                )
            continue

        host = str(payload.get("hostname") or "")
        lock = WorkspaceLock(
            lock_path,
            workspace_id=str(payload.get("workspace_id") or "<unknown>"),
            task_id=str(payload.get("task_id") or "<unknown>"),
            operation="doctor-repair",
            timeout_seconds=config.state.lock_timeout_seconds,
            heartbeat_seconds=config.state.heartbeat_seconds,
        )
        status = lock.inspect()
        if not status.stale:
            report.skipped += 1
            report.items.append(
                LockRepairItem(
                    path=normalize_windows_path(lock_path),
                    action="kept",
                    reason=status.reason or "lock is active",
                )
            )
            continue

        if host and host != current_host and not force:
            report.skipped += 1
            report.items.append(
                LockRepairItem(
                    path=normalize_windows_path(lock_path),
                    action="skipped",
                    reason="different hostname; use --force to clear",
                )
            )
            continue

        lock_path.unlink(missing_ok=True)
        report.repaired += 1
        report.items.append(
            LockRepairItem(
                path=normalize_windows_path(lock_path),
                action="removed",
                reason=status.reason or "stale lock",
            )
        )
    return report
