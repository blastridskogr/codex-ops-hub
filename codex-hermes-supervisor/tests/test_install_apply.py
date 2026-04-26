from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.install_global import apply_install_global, rollback_install_global


def test_apply_install_global_writes_user_scoped_files(tmp_path) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "config.toml").write_text('approval_policy = "never"\n', encoding="utf-8")
    (home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)

    result = apply_install_global(SupervisorConfig(), user_home=home)

    config_text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.codex_hermes_supervisor]" in config_text
    assert (home / ".codex" / "agents" / "atlas.toml").exists()
    assert "developer_instructions" in (home / ".codex" / "agents" / "atlas.toml").read_text(encoding="utf-8")
    skill_path = home / ".codex" / "skills" / "codex-hermes-harness" / "SKILL.md"
    assert skill_path.exists()
    skill_text = skill_path.read_text(encoding="utf-8")
    assert skill_text.startswith("---\n")
    assert "Managed by Codex-Hermes Official LLM Wiki Harness" in skill_text
    assert (home / ".codex-hermes" / "config.yaml").exists()
    assert (home / ".codex-hermes" / "logs").exists()
    assert result.backup_dir.exists()


def test_rollback_install_global_restores_previous_config(tmp_path) -> None:
    home = tmp_path / "home"
    config_path = home / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('approval_policy = "never"\n', encoding="utf-8")
    (home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)

    result = apply_install_global(SupervisorConfig(), user_home=home)
    timestamp = result.backup_dir.name
    config_path.write_text('approval_policy = "on-request"\n', encoding="utf-8")

    restored = rollback_install_global(timestamp, user_home=home)
    assert config_path in restored
    assert config_path.read_text(encoding="utf-8") == 'approval_policy = "never"\n'
    assert not (home / ".codex" / "agents" / "atlas.toml").exists()
    assert not (home / ".codex" / "skills" / "codex-hermes-harness" / "SKILL.md").exists()


def test_apply_install_global_rejects_invalid_agent_template(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "config.toml").write_text("", encoding="utf-8")
    (home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.install_global._CUSTOM_AGENTS",
        {"prometheus.toml": 'name = "prometheus"\ndescription = "planner"\n'},
    )
    try:
        apply_install_global(SupervisorConfig(), user_home=home)
    except ValueError as exc:
        assert "prometheus.toml" in str(exc)
    else:
        raise AssertionError("expected invalid managed agent template to fail")
