from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.source_intake import (
    codex_session_compile,
    codex_session_ingest,
    source_compile,
    source_ingest,
    source_review,
    source_status,
)


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    (repo / "README.md").write_text("# project\n\nA source-intake sample.\n", encoding="utf-8")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "init"], repo)
    return repo


def _config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state")},
            "hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder"},
            "obsidian": {"enabled": True, "vault_root": str(tmp_path / "vault"), "wiki_root": "CodexWiki"},
        }
    )


def _patch_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")


def test_source_ingest_dry_run_does_not_write_manifest(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)

    result = source_ingest(repo, Path("README.md"), dry_run=True)

    assert result.dry_run is True
    assert result.entry.source_id.startswith("src-")
    assert result.entry.review_status == "not_required"
    assert not Path(result.manifest_path).exists()


def test_source_ingest_apply_writes_manifest_and_status(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)

    result = source_ingest(repo, Path("README.md"), dry_run=False)
    status = source_status(repo)

    assert result.created is True
    assert Path(result.manifest_path).exists()
    assert status.exists is True
    assert status.total_entries == 1
    assert status.pending_review == 0


def test_private_source_compile_requires_review(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    ingest = source_ingest(repo, Path("README.md"), privacy="private", dry_run=False)

    result = source_compile(config, repo, ingest.entry.source_id, dry_run=True)

    assert "SOURCE_REVIEW_REQUIRED_BEFORE_COMPILE" in result.warnings
    assert result.compiled is False
    assert result.planned_note_path is not None


def test_source_review_apply_allows_compile_plan(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    ingest = source_ingest(repo, Path("README.md"), privacy="private", dry_run=False)

    reviewed = source_review(repo, ingest.entry.source_id, reviewer="tester", dry_run=False)
    result = source_compile(config, repo, ingest.entry.source_id, dry_run=True)

    assert reviewed.entry.review_status == "reviewed"
    assert reviewed.entry.reviewed_by == "tester"
    assert "SOURCE_REVIEW_REQUIRED_BEFORE_COMPILE" not in result.warnings


def test_source_compile_apply_writes_reference_only_source_note(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    ingest = source_ingest(repo, Path("README.md"), privacy="private", dry_run=False)

    result = source_compile(config, repo, ingest.entry.source_id, dry_run=False)
    status = source_status(repo)

    assert result.compiled is True
    assert result.planned_note_path is not None
    note_path = Path(result.planned_note_path)
    assert note_path.exists()
    text = note_path.read_text(encoding="utf-8")
    assert "evidence_class: reference_only" in text
    assert "review_status: unverified" in text
    assert "This is an Official LLM Wiki compiled source note." in text
    assert "A source-intake sample" not in text
    assert status.compiled == 1


def test_source_compile_frontmatter_keeps_evidence_allowed_runtime_only(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    ingest = source_ingest(repo, Path("README.md"), dry_run=False)

    result = source_compile(config, repo, ingest.entry.source_id, dry_run=True)
    frontmatter = result.frontmatter.model_dump()

    assert result.dry_run is True
    assert result.frontmatter.memory_kind == "source"
    assert result.frontmatter.source_hashes == [ingest.entry.sha256]
    assert "evidence_allowed" not in frontmatter


def test_codex_session_ingest_registers_private_conversations_by_session_cwd(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    codex_home = tmp_path / ".codex"
    session_dir = codex_home / "sessions" / "2026" / "04" / "29"
    session_dir.mkdir(parents=True)
    session_id = "019dd5b6-32f6-79d1-b8eb-6c6da0cd690c"
    session_path = session_dir / f"rollout-2026-04-29T05-09-40-{session_id}.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-29T00:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": "2026-04-29T00:00:00Z",
                    "cwd": str(repo),
                    "agent_role": "oracle",
                    "agent_nickname": "Verifier",
                    "model": "gpt-5.5",
                },
            }
        )
        + "\n"
        + json.dumps({"type": "message", "payload": {"role": "user", "content": "verify this"}})
        + "\n",
        encoding="utf-8",
    )
    (codex_home / "session_index.jsonl").write_text(
        json.dumps({"id": session_id, "thread_name": "Verify thread", "updated_at": "2026-04-29T00:01:00Z"}) + "\n",
        encoding="utf-8",
    )

    dry_run = codex_session_ingest(codex_home=codex_home, dry_run=True)
    assert dry_run.created == 1
    assert dry_run.project_summaries[0].repo_root == str(repo.resolve())
    assert not Path(dry_run.project_summaries[0].manifest_path).exists()

    applied = codex_session_ingest(codex_home=codex_home, dry_run=False)
    status = source_status(repo)

    assert applied.created == 1
    assert status.total_entries == 1
    assert status.pending_review == 1
    manifest_text = Path(status.manifest_path).read_text(encoding="utf-8")
    assert "source_type: conversation" in manifest_text
    assert "privacy: private" in manifest_text
    assert "review_status: pending" in manifest_text
    assert "Verify thread" in manifest_text


def test_codex_session_ingest_skips_backups_by_default(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    codex_home = tmp_path / ".codex"
    session_dir = codex_home / "sessions" / "2026" / "04" / "29"
    session_dir.mkdir(parents=True)
    payload = {
        "timestamp": "2026-04-29T00:00:00Z",
        "type": "session_meta",
        "payload": {"id": "019dd5b6-aaaa-79d1-b8eb-6c6da0cd690c", "cwd": str(repo)},
    }
    (session_dir / "rollout-2026-04-29T00-00-00-019dd5b6-aaaa-79d1-b8eb-6c6da0cd690c.jsonl.bak").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    result = codex_session_ingest(codex_home=codex_home, dry_run=True)

    assert result.scanned_files == 0
    assert result.created == 0


def test_codex_session_compile_writes_conversation_source_notes(tmp_path: Path, monkeypatch) -> None:
    _patch_home(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    codex_home = tmp_path / ".codex"
    session_dir = codex_home / "sessions" / "2026" / "04" / "29"
    session_dir.mkdir(parents=True)
    session_id = "019dd5b6-32f6-79d1-b8eb-6c6da0cd690c"
    session_path = session_dir / f"rollout-2026-04-29T05-09-40-{session_id}.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-29T00:00:00Z",
                "type": "session_meta",
                "payload": {"id": session_id, "timestamp": "2026-04-29T00:00:00Z", "cwd": str(repo)},
            }
        )
        + "\n"
        + json.dumps({"type": "message", "payload": {"role": "user", "content": "raw transcript text should not be copied"}})
        + "\n",
        encoding="utf-8",
    )
    codex_session_ingest(codex_home=codex_home, dry_run=False)
    project_id = source_status(repo).project_id

    result = codex_session_compile(config, project_id=project_id, dry_run=False)
    status = source_status(repo)
    note_dir = Path(config.obsidian.vault_root) / config.obsidian.wiki_root / "Sources" / project_id

    assert result.compiled == 1
    assert status.compiled == 1
    index_text = (note_dir / "conversation-index.md").read_text(encoding="utf-8")
    assert f"](.//" not in index_text
    notes = [path for path in note_dir.glob("conv-*.md")]
    assert len(notes) == 1
    assert f"[`{notes[0].stem}`](./{notes[0].name})" in index_text
    note_text = notes[0].read_text(encoding="utf-8")
    assert "source_type: conversation" in note_text
    assert "privacy: private" in note_text
    assert "evidence_class: reference_only" in note_text
    assert "raw transcript text should not be copied" not in note_text
