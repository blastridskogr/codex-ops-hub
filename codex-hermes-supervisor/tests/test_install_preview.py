from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.install_preview import build_install_preview


def test_install_preview_detects_existing_codex_skill_root(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    preview = build_install_preview(SupervisorConfig(), user_home=home)
    assert preview.skill_root.endswith(r".codex\skills")
    assert preview.agent_root.status == "would_create"


def test_install_preview_reports_files_without_writing(tmp_path) -> None:
    home = tmp_path / "home"
    preview = build_install_preview(SupervisorConfig(), user_home=home)
    assert preview.codex_config_exists is False
    assert preview.agent_files
    assert preview.skill_files
    assert not (home / ".codex").exists()


def test_install_preview_rejects_invalid_agent_template(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.install_preview.get_managed_agent_templates",
        lambda: {"prometheus.toml": 'name = "prometheus"\ndescription = "planner"\n'},
    )
    try:
        build_install_preview(SupervisorConfig(), user_home=home)
    except ValueError as exc:
        assert "prometheus.toml" in str(exc)
    else:
        raise AssertionError("expected invalid agent template to fail install preview")
