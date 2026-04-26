from __future__ import annotations

import pytest

from codex_hermes_supervisor.core.idempotency import (
    IdempotencyMismatchError,
    natural_key,
    record_result,
    validate_existing,
)
from codex_hermes_supervisor.core.state_store import WorkspaceStateStore


def test_idempotency_record_round_trip(tmp_path) -> None:
    store = WorkspaceStateStore(tmp_path, "workspace-1")
    key = natural_key("version_prepare", "task-1", "tools/TEST.py")
    payload = {"active_path": "tools/TEST.py"}
    record_result(
        store,
        key=key,
        tool="version_prepare",
        task_id="task-1",
        payload=payload,
        result={"version_path": "tools/TEST_V0.1.py"},
    )
    record = validate_existing(store, key, payload)
    assert record is not None
    assert record.result["version_path"] == "tools/TEST_V0.1.py"


def test_idempotency_payload_mismatch(tmp_path) -> None:
    store = WorkspaceStateStore(tmp_path, "workspace-1")
    key = natural_key("version_prepare", "task-1", "tools/TEST.py")
    record_result(
        store,
        key=key,
        tool="version_prepare",
        task_id="task-1",
        payload={"active_path": "tools/TEST.py"},
        result={"version_path": "tools/TEST_V0.1.py"},
    )
    with pytest.raises(IdempotencyMismatchError):
        validate_existing(store, key, {"active_path": "tools/OTHER.py"})
