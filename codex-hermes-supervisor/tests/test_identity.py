from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.core import identity


def test_workspace_id_uses_toplevel(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(identity, "git_show_toplevel", lambda cwd: repo_root)
    result = identity.compute_workspace_id(repo_root)
    assert result.endswith("-repo")


def test_project_id_prefers_remote(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(identity, "git_remote_origin_url", lambda cwd: "git@github.com:org/sample.git")
    monkeypatch.setattr(identity, "git_common_dir", lambda cwd: None)
    project_id, remote, common_dir = identity.compute_project_id(repo_root)
    assert remote == "git@github.com:org/sample.git"
    assert common_dir is None
    assert project_id.endswith("-sample")


def test_project_id_falls_back_to_common_dir(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(identity, "git_remote_origin_url", lambda cwd: None)
    monkeypatch.setattr(identity, "git_common_dir", lambda cwd: tmp_path / ".git")
    project_id, remote, common_dir = identity.compute_project_id(repo_root)
    assert remote is None
    assert common_dir is not None
    assert project_id.endswith("-repo")
