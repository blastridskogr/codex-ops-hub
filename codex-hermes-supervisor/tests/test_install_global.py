from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.install_global import apply_install_global
from codex_hermes_supervisor.services.install_preview import build_install_preview


def test_install_global_creates_project_templates(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex" / "skills").mkdir(parents=True)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    result = apply_install_global(SupervisorConfig(), user_home=home)

    template_root = home / ".codex" / "templates" / "codex-hermes-project"
    assert template_root.exists()
    assert (template_root / ".codex" / "config.toml").exists()
    assert (template_root / "AGENTS.md").exists()
    assert (template_root / "README.md").exists()
    assert any(path.is_relative_to(template_root) for path in result.modified_files)


def test_install_global_skills_start_with_yaml_frontmatter(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex" / "skills").mkdir(parents=True)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    apply_install_global(SupervisorConfig(), user_home=home)

    skill_root = home / ".codex" / "skills"
    for skill_name in ("codex-hermes-harness", "official-llm-wiki", "versioned-files"):
        skill_text = (skill_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert skill_text.startswith("---\n")
        assert "Managed by Codex-Hermes Official LLM Wiki Harness" in skill_text


def test_install_global_preview_includes_project_templates(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    preview = build_install_preview(SupervisorConfig(), user_home=home)

    template_paths = {item.path for item in preview.project_template_files}
    template_root = home / ".codex" / "templates" / "codex-hermes-project"
    assert str(template_root / ".codex" / "config.toml") in template_paths
    assert str(template_root / "AGENTS.md") in template_paths
    assert str(template_root / "README.md") in template_paths
    assert {item.status for item in preview.project_template_files} == {"would_create"}


def test_install_global_replaces_existing_agents_block_with_windows_paths(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    agents_path = home / ".codex" / "AGENTS.md"
    agents_path.parent.mkdir(parents=True)
    agents_path.write_text(
        "before\n<!-- BEGIN CODEX-HERMES-HARNESS -->\nold-block-sentinel\n<!-- END CODEX-HERMES-HARNESS -->\nafter\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    apply_install_global(SupervisorConfig(), user_home=home)

    text = agents_path.read_text(encoding="utf-8")
    assert "before" in text
    assert "after" in text
    assert "%USERPROFILE%\\.codex\\templates\\codex-hermes-project" in text
    assert "old-block-sentinel" not in text
