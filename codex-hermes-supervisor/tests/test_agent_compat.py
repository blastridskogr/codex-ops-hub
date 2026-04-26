from __future__ import annotations

import json

from codex_hermes_supervisor.services.agent_compat import (
    build_codex_compat_report,
    repair_managed_agents,
    validate_agent_toml_content,
    validate_generated_agent_templates,
)


def test_validate_agent_toml_content_requires_developer_instructions() -> None:
    result = validate_agent_toml_content(
        'name = "prometheus"\n'
        'description = "planner"\n'
        'sandbox_mode = "read-only"\n'
    )
    assert result.valid is False
    assert "missing required field: developer_instructions" in result.errors


def test_validate_generated_agent_templates_raises_for_invalid_template() -> None:
    try:
        validate_generated_agent_templates({"prometheus.toml": 'name = "prometheus"\ndescription = "planner"\n'})
    except ValueError as exc:
        assert "prometheus.toml" in str(exc)
    else:
        raise AssertionError("expected invalid template validation to raise")


def test_build_codex_compat_report_detects_missing_developer_instructions(tmp_path) -> None:
    home = tmp_path / "home"
    agent_root = home / ".codex" / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "prometheus.toml").write_text(
        '# managed_id = "codex-hermes-harness"\n'
        'name = "prometheus"\n'
        'description = "planner"\n',
        encoding="utf-8",
    )
    report = build_codex_compat_report(
        user_home=home,
        templates={"prometheus.toml": 'name = "prometheus"\ndescription = "planner"\ndeveloper_instructions = "x"\n'},
    )
    checks = {check.name: check for check in report.checks}
    assert checks["agent:prometheus.toml"].status == "error"
    assert "developer_instructions" in checks["agent:prometheus.toml"].details


def test_repair_managed_agents_dry_run_reports_update(tmp_path) -> None:
    home = tmp_path / "home"
    agent_root = home / ".codex" / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    target = agent_root / "prometheus.toml"
    target.write_text(
        '# Managed by Codex-Hermes Official LLM Wiki Harness\n'
        '# managed_id = "codex-hermes-harness"\n'
        '# managed_schema_version = 1\n\n'
        'name = "prometheus"\n'
        'description = "planner"\n',
        encoding="utf-8",
    )
    report = repair_managed_agents(
        templates={"prometheus.toml": 'name = "prometheus"\ndescription = "planner"\ndeveloper_instructions = "x"\n'},
        user_home=home,
        dry_run=True,
    )
    assert report.actions[0].action == "would_update"
    assert target.read_text(encoding="utf-8").endswith('description = "planner"\n')


def test_repair_managed_agents_apply_rewrites_managed_file(tmp_path) -> None:
    home = tmp_path / "home"
    agent_root = home / ".codex" / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    target = agent_root / "prometheus.toml"
    target.write_text(
        '# Managed by Codex-Hermes Official LLM Wiki Harness\n'
        '# managed_id = "codex-hermes-harness"\n'
        '# managed_schema_version = 1\n\n'
        'name = "prometheus"\n'
        'description = "planner"\n',
        encoding="utf-8",
    )
    report = repair_managed_agents(
        templates={"prometheus.toml": '# Managed by Codex-Hermes Official LLM Wiki Harness\n# managed_id = "codex-hermes-harness"\n# managed_schema_version = 1\n\nname = "prometheus"\ndescription = "planner"\ndeveloper_instructions = "x"\n'},
        user_home=home,
        dry_run=False,
    )
    assert report.actions[0].action == "update"
    assert 'developer_instructions = "x"' in target.read_text(encoding="utf-8")
    assert target.with_name("prometheus.toml.bak").exists()
