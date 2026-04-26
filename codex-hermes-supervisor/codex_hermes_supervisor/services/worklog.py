"""Worklog service wrapper."""

from __future__ import annotations

from pathlib import Path

from .task_records import append_worklog_entry


def record_worklog(tasks_dir: Path, entry: str, *, source: str = "user") -> Path:
    return append_worklog_entry(tasks_dir, entry, source=source)
