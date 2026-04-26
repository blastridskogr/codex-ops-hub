from __future__ import annotations

import json

from typer.testing import CliRunner

from codex_hermes_supervisor.cli import app


def test_hermes_sync_outbox_returns_friendly_error(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    runner = CliRunner()
    result = runner.invoke(app, ["hermes-sync-outbox"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["code"] == "HERMES_SYNC_REQUIRES_DIRECT_MODE"


def test_hermes_sync_outbox_supports_builtin_file_backend(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    from codex_hermes_supervisor.integrations.hermes import write_outbox_note

    outbox_root = home / ".codex-hermes" / "hermes_outbox"
    write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="CLI builtin-file sync.",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["hermes-sync-outbox", "--backend", "builtin_file"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["imported"]


def test_hermes_selftest_supports_backend_override(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    runner = CliRunner()
    result = runner.invoke(app, ["hermes-selftest", "--backend", "builtin_file"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["actual_write_mode"] == "builtin_file"
    assert payload["recall_contains_token"] is False
    assert payload["write_probe_performed"] is False


def test_vector_cli_commands(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    runner = CliRunner()

    doctor = runner.invoke(app, ["vector-doctor"])
    assert doctor.exit_code == 0
    doctor_payload = json.loads(doctor.stdout)
    assert doctor_payload["backend"] == "vector_local"

    reindex = runner.invoke(app, ["vector-reindex"])
    assert reindex.exit_code == 0


def test_qmd_sync_cli(monkeypatch, tmp_path) -> None:
    from codex_hermes_supervisor.integrations.qmd import QmdSyncReport

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr(
        "codex_hermes_supervisor.cli.sync_qmd_collections",
        lambda config, embed=False, force_embed=False: QmdSyncReport(
            executable=config.search.qmd.executable,
            synced_collections=[{"name": "codexwiki", "path": str(tmp_path / "vault")}],
            embed_requested=embed,
            embed_completed=embed,
            warnings=[],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["qmd-sync", "--embed"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["embed_requested"] is True


def test_qmd_eval_returns_nonzero_when_gate_fails(monkeypatch, tmp_path) -> None:
    from codex_hermes_supervisor.services.qmd_eval import QmdEvalReport

    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"name":"gate-fixture","cases":[]}', encoding="utf-8")
    monkeypatch.setattr(
        "codex_hermes_supervisor.cli.evaluate_qmd_fixture",
        lambda config, fixture_path: QmdEvalReport(
            fixture_name="gate-fixture",
            total_cases=1,
            top1_hits=0,
            top3_hits=0,
            topk_hits=0,
            forbidden_cases=0,
            backend_mismatch_cases=0,
            top1_rate=0.0,
            top3_rate=0.0,
            topk_rate=0.0,
            case_results=[],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["qmd-eval", "--fixture", str(fixture), "--min-top1-rate", "1.0"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["gate"]["ok"] is False


def test_search_eval_runs_matrix_fixture(monkeypatch, tmp_path) -> None:
    from codex_hermes_supervisor.services.search_eval import SearchEvalBackendSummary, SearchEvalReport

    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"name":"search-fixture","cases":[]}', encoding="utf-8")
    monkeypatch.setattr(
        "codex_hermes_supervisor.cli.evaluate_search_fixture",
        lambda config, fixture_path: SearchEvalReport(
            fixture_name="search-fixture",
            total_cases=2,
            top1_hits=2,
            top3_hits=2,
            topk_hits=2,
            forbidden_cases=0,
            backend_mismatch_cases=0,
            top1_rate=1.0,
            top3_rate=1.0,
            topk_rate=1.0,
            backend_summaries=[
                SearchEvalBackendSummary(
                    backend="qmd",
                    total_cases=1,
                    top1_hits=1,
                    top3_hits=1,
                    topk_hits=1,
                    forbidden_cases=0,
                    backend_mismatch_cases=0,
                    top1_rate=1.0,
                    top3_rate=1.0,
                    topk_rate=1.0,
                )
            ],
            case_results=[],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["search-eval", "--fixture", str(fixture)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["report"]["backend_summaries"][0]["backend"] == "qmd"


def test_doctor_codex_compat(monkeypatch, tmp_path) -> None:
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
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--codex-compat"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "codex_compat" in payload
    assert payload["codex_compat"]["notes"]


def test_repair_agents_dry_run(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    agent_root = home / ".codex" / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "prometheus.toml").write_text(
        '# Managed by Codex-Hermes Official LLM Wiki Harness\n'
        '# managed_id = "codex-hermes-harness"\n'
        '# managed_schema_version = 1\n\n'
        'name = "prometheus"\n'
        'description = "planner"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    runner = CliRunner()
    result = runner.invoke(app, ["repair-agents", "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert any(item["action"] == "would_update" for item in payload["actions"])


def test_repair_agents_apply(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    runner = CliRunner()
    result = runner.invoke(app, ["repair-agents"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert any(item["action"] == "update" for item in payload["actions"])
    assert "developer_instructions" in target.read_text(encoding="utf-8")


def test_agents_lint_blocks_project_rule_weakening(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    codex_root = home / ".codex"
    codex_root.mkdir(parents=True)
    (codex_root / "AGENTS.md").write_text("Use harness_finish as the formal completion gate.\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("For this project, skip harness_finish.\n", encoding="utf-8")
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.codex_root", lambda: codex_root)

    runner = CliRunner()
    result = runner.invoke(app, ["agents-lint", "--repo", str(repo)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any(item["code"] == "HARNESS_FINISH_BYPASS" for item in payload["findings"])
