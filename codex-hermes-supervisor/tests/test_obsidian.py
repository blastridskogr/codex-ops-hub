from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.integrations.obsidian import initialize_obsidian_vault, write_wiki_note
from codex_hermes_supervisor.schemas.wiki import WikiNoteInput


def _note(
    *,
    task_id: str,
    title: str,
    summary: str = "summary",
) -> WikiNoteInput:
    return WikiNoteInput(
        repo_root=r"C:\repo",
        task_id=task_id,
        type="task",
        title=title,
        summary=summary,
        evidence=["evidence"],
        next_steps=["next"],
    )


def test_write_wiki_note_disabled_mode() -> None:
    data = write_wiki_note(None, "CodexWiki", "project-1", _note(task_id="task-1", title="My Note"))
    assert data.disabled is True
    assert data.created is False


def test_write_wiki_note_updates_same_note_key(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    first = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-1", title="My Note", summary="one"))
    second = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-1", title="My Note", summary="two"))
    assert first.path == second.path
    path = Path(second.path or "")
    assert "two" in path.read_text(encoding="utf-8")


def test_write_wiki_note_suffixes_slug_collision_for_different_key(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    first = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-1", title="My Note"))
    second = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-2", title="My Note"))
    assert first.path != second.path
    assert second.path and second.path.endswith(".md")
    assert Path(first.path or "").stem != Path(second.path or "").stem


def test_initialize_obsidian_vault_creates_expected_structure(tmp_path: Path) -> None:
    wiki_dir = initialize_obsidian_vault(tmp_path / "vault", "CodexWiki")
    assert (wiki_dir / "_index.md").exists()
    assert (wiki_dir / "_schema" / "WIKI_SCHEMA.md").exists()
    for dirname in ["Projects", "Tasks", "Decisions", "Bugs", "Workflows", "Sources"]:
        assert (wiki_dir / dirname).exists()


def test_write_wiki_note_preserves_created_timestamp_on_update(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    first = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-1", title="My Note", summary="one"))
    path = Path(first.path or "")
    original = path.read_text(encoding="utf-8")
    created_line = next(line for line in original.splitlines() if line.startswith('created: "'))

    second = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-1", title="My Note", summary="two"))
    updated = Path(second.path or "").read_text(encoding="utf-8")
    assert created_line in updated
    assert 'confidence: "medium"' in updated
    assert "## Links" in updated


def test_write_wiki_note_caps_long_filename_with_hash(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    title = "Very Long Task Title " * 40
    data = write_wiki_note(vault, "CodexWiki", "project-1", _note(task_id="task-1", title=title))
    assert data.created is True
    path = Path(data.path or "")
    assert path.exists()
    assert len(path.name) < 180
    assert path.stem.count("-") >= 1
