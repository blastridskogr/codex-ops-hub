"""Hermes outbox lifecycle helpers."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.identity import normalize_windows_path


class OutboxItem(BaseModel):
    path: str
    status: str
    bucket: str


class OutboxReport(BaseModel):
    items: list[OutboxItem] = Field(default_factory=list)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _replace_status(text: str, new_status: str) -> str:
    lines = text.splitlines()
    updated = False
    for index, line in enumerate(lines):
        if line.startswith("status: "):
            lines[index] = f"status: {new_status}"
            updated = True
            break
    if not updated:
        lines.insert(1, f"status: {new_status}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") or not lines else "")


def list_outbox(outbox_root: Path) -> OutboxReport:
    """List outbox note files and their lifecycle status."""

    report = OutboxReport()
    for path in sorted(outbox_root.rglob("*.md")):
        text = _read_text(path)
        status = "unknown"
        for line in text.splitlines():
            if line.startswith("status: "):
                status = line.split(":", 1)[1].strip()
                break
        bucket = path.parent.name
        report.items.append(OutboxItem(path=normalize_windows_path(path), status=status, bucket=bucket))
    return report


def mark_outbox_imported(path: Path) -> Path:
    """Update an outbox item to imported status."""

    text = _read_text(path)
    updated = _replace_status(text, "imported")
    atomic_write_text(path, updated)
    return path


def archive_imported_outbox(outbox_root: Path) -> list[Path]:
    """Move imported outbox items into an archived subdirectory per bucket."""

    archived: list[Path] = []
    for path in sorted(outbox_root.rglob("*.md")):
        if "archived" in {part.lower() for part in path.parts}:
            continue
        text = _read_text(path)
        if "status: imported" not in text:
            continue
        target_dir = path.parent / "archived"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            target.unlink()
        path.replace(target)
        archived.append(target)
    return archived
