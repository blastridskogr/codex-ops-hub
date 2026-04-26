from __future__ import annotations

import json
from pathlib import Path

from codex_hermes_supervisor.core import locks


def test_workspace_lock_writes_and_removes_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    lock = locks.WorkspaceLock(
        lock_path,
        workspace_id="workspace-1",
        task_id="task-1",
        operation="harness_begin",
        timeout_seconds=30,
        heartbeat_seconds=60,
    )
    lock.acquire()
    try:
        assert lock_path.exists()
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["workspace_id"] == "workspace-1"
        assert payload["task_id"] == "task-1"
    finally:
        lock.release()
    assert not lock_path.exists()


def test_stale_lock_detected_when_pid_missing(monkeypatch, tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    payload = {
        "lock_schema_version": 1,
        "pid": 999999,
        "hostname": locks.socket.gethostname(),
        "created_at": locks.now_local_iso(),
        "updated_at": locks.now_local_iso(),
        "workspace_id": "workspace-1",
        "task_id": "task-1",
        "operation": "harness_finish",
    }
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(locks, "_pid_exists", lambda pid: False)
    status = locks.WorkspaceLock(lock_path, workspace_id="workspace-1", task_id="task-1", operation="x").inspect()
    assert status.stale is True
