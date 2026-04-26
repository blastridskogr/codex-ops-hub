"""Global task record helpers."""

from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.schemas.tools import TaskRecordSummary
from codex_hermes_supervisor.schemas.versioning import ManagedFileState
from codex_hermes_supervisor.services.versioning import prepare_version, sync_version

_RECORD_NAMES = ("todo", "worklog", "lessons")


class TaskRecordError(RuntimeError):
    """Raised when task records cannot be updated."""


def _active_path(tasks_dir: Path, name: str) -> Path:
    return tasks_dir / f"{name}.md"


def _existing_state(tasks_dir: Path, name: str) -> ManagedFileState:
    active_path = _active_path(tasks_dir, name)
    versions = sorted(str(path) for path in active_path.parent.glob(f"{name}_V*.md"))
    return ManagedFileState(
        active_path=str(active_path),
        versions=versions,
        synced=bool(active_path.exists()),
    )


def ensure_task_records(tasks_dir: Path) -> None:
    """Ensure built-in task record files exist."""

    tasks_dir.mkdir(parents=True, exist_ok=True)
    for name in _RECORD_NAMES:
        active_path = _active_path(tasks_dir, name)
        if active_path.exists():
            continue
        state = _existing_state(tasks_dir, name)
        prepare_data, state = prepare_version(active_path, allow_create=True, existing_state=state)
        _, _ = sync_version(active_path, Path(prepare_data.version_path), existing_state=state)


def _write_record(tasks_dir: Path, name: str, content: str) -> Path:
    ensure_task_records(tasks_dir)
    active_path = _active_path(tasks_dir, name)
    state = _existing_state(tasks_dir, name)
    prepare_data, state = prepare_version(active_path, existing_state=state)
    version_path = Path(prepare_data.version_path)
    version_path.write_text(content, encoding="utf-8", newline="")
    _, _ = sync_version(active_path, version_path, existing_state=state)
    return active_path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def set_todo(tasks_dir: Path, content: str) -> Path:
    """Replace the current todo content."""

    return _write_record(tasks_dir, "todo", content.rstrip() + ("\n" if content and not content.endswith("\n") else ""))


def append_worklog_entry(tasks_dir: Path, entry: str, *, source: str = "user") -> Path:
    """Append a transaction-level worklog entry unless it is internal recursion."""

    if source == "task_records_sync":
        return _active_path(tasks_dir, "worklog")
    existing = _read_text(_active_path(tasks_dir, "worklog"))
    normalized_entry = entry.strip()
    content = existing.rstrip()
    if content:
        content += "\n\n"
    content += normalized_entry
    if not content.endswith("\n"):
        content += "\n"
    return _write_record(tasks_dir, "worklog", content)


def append_lesson(tasks_dir: Path, lesson: str) -> Path:
    """Append a lesson bullet."""

    existing = _read_text(_active_path(tasks_dir, "lessons"))
    lines = [line for line in existing.splitlines() if line.strip()]
    bullet = lesson.strip()
    if not bullet.startswith("- "):
        bullet = f"- {bullet}"
    if bullet not in lines:
        lines.append(bullet)
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    return _write_record(tasks_dir, "lessons", content)


def build_task_record_summary(tasks_dir: Path) -> TaskRecordSummary:
    """Return the compact summary exposed through MCP."""

    ensure_task_records(tasks_dir)
    todo_text = _read_text(_active_path(tasks_dir, "todo")).strip()
    lessons_text = _read_text(_active_path(tasks_dir, "lessons")).splitlines()

    if "- [" in todo_text:
        total = sum(1 for line in todo_text.splitlines() if line.strip().startswith("- ["))
        complete = sum(1 for line in todo_text.splitlines() if line.strip().startswith("- [x]"))
        todo_summary = f"{total} items planned, {complete} complete."
    else:
        todo_summary = todo_text[:160]

    latest_lessons = [line[2:] if line.startswith("- ") else line for line in lessons_text if line.strip()][-3:]
    return TaskRecordSummary(todo=todo_summary, latest_lessons=latest_lessons)
