from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.services.install_project import (
    ensure_project_templates,
    install_project_config,
    project_config_preview,
    project_template_root,
)


def test_project_config_preview_and_apply(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    repo = tmp_path / "repo"
    repo.mkdir()

    preview = project_config_preview(repo)
    assert preview["would_change"] is True
    assert preview["template_root"].endswith("\\.codex\\templates\\codex-hermes-project")
    assert str(repo / "AGENTS.md") in preview["would_change_paths"]

    result = install_project_config(repo, dry_run=False)
    assert result.changed is True
    assert result.template_root == project_template_root()
    config_path = repo / ".codex" / "config.toml"
    agents_path = repo / "AGENTS.md"
    assert config_path.exists()
    assert agents_path.exists()
    assert "[mcp_servers.codex_hermes_supervisor]" in config_path.read_text(encoding="utf-8")
    agents_text = agents_path.read_text(encoding="utf-8")
    assert "Project identity:" in agents_text
    assert "project_id:" in agents_text
    assert "must not weaken the global Codex-Hermes rules" in agents_text


def test_project_config_apply_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    repo = tmp_path / "repo"
    repo.mkdir()

    first = install_project_config(repo, dry_run=False)
    second = install_project_config(repo, dry_run=False)

    assert first.changed is True
    assert second.changed is False


def test_global_project_templates_are_created_under_codex_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    changed = ensure_project_templates()

    root = home / ".codex" / "templates" / "codex-hermes-project"
    assert root in {path.parent for path in changed} or changed
    assert (root / ".codex" / "config.toml").exists()
    assert (root / "AGENTS.md").exists()
    assert (root / "README.md").exists()
