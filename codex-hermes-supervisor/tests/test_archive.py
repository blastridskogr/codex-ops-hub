from __future__ import annotations

from codex_hermes_supervisor.core.archive import archive_active_state
from codex_hermes_supervisor.core.state_store import WorkspaceStateStore
from codex_hermes_supervisor.schemas.state import TaskState


def test_archive_active_state_copies_current_files(tmp_path) -> None:
    store = WorkspaceStateStore(tmp_path, "workspace-1")
    state = TaskState(
        task_id="task-1",
        task="fix bug",
        repo_root=r"C:\repo",
        workspace_id="workspace-1",
        project_id="project-1",
        created_at="2026-04-21T18:55:33+09:00",
        updated_at="2026-04-21T18:55:33+09:00",
        phase="PLANNED",
    )
    store.save_state(state)
    store.tasks_dir.mkdir(parents=True, exist_ok=True)
    (store.tasks_dir / "todo.md").write_text("- item", encoding="utf-8")
    archived = archive_active_state(store, state, reason="force_new_task")
    assert (archived / "state.json").exists()
    assert (archived / "tasks" / "todo.md").exists()
    assert (archived / "archive_metadata.json").exists()
