from __future__ import annotations

from codex_hermes_supervisor.services.lessons import record_lesson
from codex_hermes_supervisor.services.task_records import build_task_record_summary, ensure_task_records, set_todo
from codex_hermes_supervisor.services.worklog import record_worklog


def test_task_records_create_initial_files(tmp_path) -> None:
    tasks_dir = tmp_path / "tasks"
    ensure_task_records(tasks_dir)
    assert (tasks_dir / "todo.md").exists()
    assert (tasks_dir / "worklog.md").exists()
    assert (tasks_dir / "lessons.md").exists()


def test_worklog_recursion_guard(tmp_path) -> None:
    tasks_dir = tmp_path / "tasks"
    ensure_task_records(tasks_dir)
    record_worklog(tasks_dir, "Did the real work", source="user")
    before = (tasks_dir / "worklog.md").read_text(encoding="utf-8")
    record_worklog(tasks_dir, "Internal sync", source="task_records_sync")
    after = (tasks_dir / "worklog.md").read_text(encoding="utf-8")
    assert before == after


def test_task_record_summary_includes_todo_and_lessons(tmp_path) -> None:
    tasks_dir = tmp_path / "tasks"
    set_todo(tasks_dir, "- [ ] first\n- [x] done\n")
    record_lesson(tasks_dir, "Inspect failing tests before asking for clarification.")
    summary = build_task_record_summary(tasks_dir)
    assert "2 items planned, 1 complete." == summary.todo
    assert summary.latest_lessons == ["Inspect failing tests before asking for clarification."]
