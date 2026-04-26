from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.services.outbox import archive_imported_outbox, list_outbox, mark_outbox_imported


def test_outbox_mark_imported_and_archive(tmp_path: Path) -> None:
    outbox_root = tmp_path / "hermes_outbox"
    handoff_dir = outbox_root / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    note_path = handoff_dir / "sample.md"
    note_path.write_text("---\nstatus: pending_import\n---\n", encoding="utf-8")

    mark_outbox_imported(note_path)
    report = list_outbox(outbox_root)
    assert report.items[0].status == "imported"

    archived = archive_imported_outbox(outbox_root)
    assert len(archived) == 1
    assert archived[0].parent.name == "archived"
    assert not note_path.exists()


def test_archive_imported_outbox_skips_existing_archived_tree(tmp_path: Path) -> None:
    outbox_root = tmp_path / "hermes_outbox"
    archived_dir = outbox_root / "handoff" / "archived"
    archived_dir.mkdir(parents=True, exist_ok=True)
    note_path = archived_dir / "already-archived.md"
    note_path.write_text("---\nstatus: imported\n---\n\n# archived\n", encoding="utf-8")

    archived = archive_imported_outbox(outbox_root)
    assert archived == []
    assert note_path.exists()
