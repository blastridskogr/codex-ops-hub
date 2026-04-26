from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from codex_hermes_supervisor.cli import app
from codex_hermes_supervisor.core.git_probe import git_show_toplevel
from codex_hermes_supervisor.services.project_git import MANAGED_GITIGNORE_BEGIN, ProjectGitBootstrapError, ensure_project_git


def _run(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), check=check, capture_output=True, text=True, encoding="utf-8")


def test_project_git_ensure_leaves_existing_repo_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    (repo / "README.md").write_text("# existing\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-m", "init"], repo)
    head_before = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    result = ensure_project_git(repo)

    assert result.already_git_repo is True
    assert result.initialized is False
    assert result.initial_commit_created is False
    assert result.head == head_before
    assert not (repo / ".gitignore").exists()


def test_project_git_ensure_initializes_non_git_without_staging_user_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "payload.txt").write_text("user data\n", encoding="utf-8")

    result = ensure_project_git(project)

    assert result.already_git_repo is False
    assert result.initialized is True
    assert result.gitignore_changed is True
    assert result.initial_commit_created is True
    assert result.head
    assert git_show_toplevel(project) == project.resolve()
    assert MANAGED_GITIGNORE_BEGIN in (project / ".gitignore").read_text(encoding="utf-8")

    tracked = _run(["git", "ls-tree", "--name-only", "HEAD"], project).stdout.splitlines()
    assert tracked == [".gitignore"]
    status = _run(["git", "status", "--porcelain=v1"], project).stdout
    assert "?? payload.txt" in status


def test_project_git_ensure_creates_baseline_for_empty_existing_repo(tmp_path: Path) -> None:
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    _run(["git", "init"], repo)

    result = ensure_project_git(repo)

    assert result.already_git_repo is True
    assert result.initialized is False
    assert result.initial_commit_created is True
    assert result.head
    assert _run(["git", "rev-parse", "--verify", "HEAD"], repo).returncode == 0


def test_project_git_ensure_refuses_existing_staged_files_before_initial_commit(tmp_path: Path) -> None:
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    (repo / "payload.txt").write_text("user data\n", encoding="utf-8")
    _run(["git", "add", "payload.txt"], repo)

    try:
        ensure_project_git(repo)
    except ProjectGitBootstrapError as exc:
        assert "non-.gitignore paths are already staged" in str(exc)
    else:  # pragma: no cover - assertion path
        raise AssertionError("expected ProjectGitBootstrapError")

    assert _run(["git", "rev-parse", "--verify", "HEAD"], repo, check=False).returncode != 0


def test_project_git_ensure_cli_dry_run_reports_actions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = CliRunner()

    result = runner.invoke(app, ["project-git-ensure", "--repo", str(project), "--dry-run"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert "git init" in payload["actions"]
    assert not (project / ".git").exists()
