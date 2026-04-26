from __future__ import annotations

from codex_hermes_supervisor.core.state_store import WorkspaceStateStore
from codex_hermes_supervisor.schemas.state import TaskState


def test_state_store_round_trip(tmp_path) -> None:
    store = WorkspaceStateStore(tmp_path, "workspace-1")
    state = TaskState(
        task_id="task-1",
        task="fix bug",
        repo_root=r"C:\repo",
        workspace_id="workspace-1",
        project_id="project-1",
        created_at="2026-04-21T18:55:33+09:00",
        updated_at="2026-04-21T18:55:33+09:00",
    )
    store.save_state(state)
    loaded = store.load_state()
    assert loaded.task_id == "task-1"
    assert loaded.workspace_id == "workspace-1"
