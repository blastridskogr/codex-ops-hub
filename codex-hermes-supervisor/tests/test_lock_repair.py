from __future__ import annotations

import json
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.services.lock_repair import repair_stale_locks


def test_repair_stale_locks_removes_same_host_dead_process(monkeypatch, tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    workspace_dir = state_root / "workspace-1"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    lock_path = workspace_dir / "lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "lock_schema_version": 1,
                "pid": 999999,
                "hostname": __import__("socket").gethostname(),
                "created_at": now_local_iso(),
                "updated_at": now_local_iso(),
                "workspace_id": "workspace-1",
                "task_id": "task-1",
                "operation": "harness_finish",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("codex_hermes_supervisor.core.locks._pid_exists", lambda pid: False)
    config = SupervisorConfig.model_validate({"state": {"root": str(state_root), "lock_timeout_seconds": 30, "heartbeat_seconds": 5}})
    report = repair_stale_locks(config)
    assert report.repaired == 1
    assert not lock_path.exists()
