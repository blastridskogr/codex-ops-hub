from __future__ import annotations

import subprocess
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.source_intake import source_compile, source_ingest, source_review, source_status


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
