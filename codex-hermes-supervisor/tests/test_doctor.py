from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.doctor import build_doctor_report


def test_doctor_report_has_expected_checks(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "config.toml").write_text("", encoding="utf-8")
    (home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_root", lambda: home / ".codex")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_config_path", lambda: home / ".codex" / "config.toml")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_agents_root", lambda: home / ".codex" / "agents")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_config_path", lambda: home / ".codex-hermes" / "config.yaml")

    report = build_doctor_report(SupervisorConfig())
    names = {check.name for check in report.checks}
    assert "python" in names
    assert "git" in names
    assert "codex_config" in names
    assert "hermes" in names
    assert "qmd" in names
    assert report.selected_skill_root.endswith(r".codex\skills")


def test_doctor_report_codex_compat_adds_agent_checks(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "config.toml").write_text("", encoding="utf-8")
    (home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "agents").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_root", lambda: home / ".codex")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_config_path", lambda: home / ".codex" / "config.toml")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_agents_root", lambda: home / ".codex" / "agents")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_config_path", lambda: home / ".codex-hermes" / "config.yaml")

    report = build_doctor_report(SupervisorConfig(), codex_compat=True)
    names = {check.name for check in report.checks}
    assert "codex_compat:codex_command" in names
    assert "codex_compat:note" in names
