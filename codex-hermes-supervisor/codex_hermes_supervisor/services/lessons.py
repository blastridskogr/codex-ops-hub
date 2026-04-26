"""Lessons service wrapper."""

from __future__ import annotations

from pathlib import Path

from .task_records import append_lesson


def record_lesson(tasks_dir: Path, lesson: str) -> Path:
    return append_lesson(tasks_dir, lesson)
