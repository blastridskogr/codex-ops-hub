"""Workspace lock handling with heartbeat and stale detection."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - optional fallback
    psutil = None

from codex_hermes_supervisor.core.atomic_write import atomic_write_text

_SUPPORTED_LOCK_SCHEMA = 1


def now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso8601(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Naive datetime is forbidden.")
    return parsed


def _pid_exists(pid: int) -> bool:
    if psutil is not None:
        return psutil.pid_exists(pid)
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    return False


@dataclass
class LockStatus:
    stale: bool
    reason: str | None = None


class LockFileError(RuntimeError):
    """Raised when the workspace lock cannot be safely acquired."""


class WorkspaceLock:
    """JSON lock file with heartbeat updates."""

    def __init__(
        self,
        lock_path: Path,
        *,
        workspace_id: str,
        task_id: str,
        operation: str,
        timeout_seconds: int = 30,
        heartbeat_seconds: int = 5,
    ) -> None:
        self.lock_path = lock_path
        self.workspace_id = workspace_id
        self.task_id = task_id
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._owned = False

    def _metadata(self) -> dict[str, object]:
        timestamp = now_local_iso()
        return {
            "lock_schema_version": _SUPPORTED_LOCK_SCHEMA,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": timestamp,
            "updated_at": timestamp,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "operation": self.operation,
        }

    def inspect(self) -> LockStatus:
        if not self.lock_path.exists():
            return LockStatus(stale=False)
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return LockStatus(stale=True, reason=f"unreadable lock: {exc}")

        version = payload.get("lock_schema_version")
        if version is None:
            return LockStatus(stale=False, reason="LOCK_SCHEMA_MISSING")
        if version != _SUPPORTED_LOCK_SCHEMA:
            return LockStatus(stale=False, reason="LOCK_SCHEMA_UNSUPPORTED")

        hostname = payload.get("hostname")
        pid = int(payload.get("pid", 0))
        updated_at = payload.get("updated_at")

        if not isinstance(updated_at, str):
            return LockStatus(stale=False, reason="LOCK_TIMESTAMP_INVALID")

        try:
            updated = _parse_iso8601(updated_at)
        except ValueError:
            return LockStatus(stale=False, reason="LOCK_TIMESTAMP_INVALID")

        if hostname == socket.gethostname() and not _pid_exists(pid):
            return LockStatus(stale=True, reason="dead owner process")

        age_seconds = (datetime.now().astimezone() - updated).total_seconds()
        if age_seconds > self.timeout_seconds:
            return LockStatus(stale=True, reason="heartbeat timeout")
        return LockStatus(stale=False)

    def _write_metadata(self, metadata: dict[str, object]) -> None:
        atomic_write_text(self.lock_path, json.dumps(metadata, indent=2, sort_keys=True))

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            status = self.inspect()
            if status.stale:
                self.lock_path.unlink(missing_ok=True)
            else:
                raise LockFileError(status.reason or "workspace is already locked")
        self._write_metadata(self._metadata())
        self._owned = True
        self._start_heartbeat()

    def _start_heartbeat(self) -> None:
        def _worker() -> None:
            while not self._stop_event.wait(self.heartbeat_seconds):
                if not self.lock_path.exists():
                    return
                try:
                    payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return
                payload["updated_at"] = now_local_iso()
                atomic_write_text(self.lock_path, json.dumps(payload, indent=2, sort_keys=True))

        self._heartbeat_thread = threading.Thread(target=_worker, name="chs-lock-heartbeat", daemon=True)
        self._heartbeat_thread.start()

    def release(self) -> None:
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=self.heartbeat_seconds + 1)
        if self._owned:
            self.lock_path.unlink(missing_ok=True)
        self._owned = False

    def __enter__(self) -> "WorkspaceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
