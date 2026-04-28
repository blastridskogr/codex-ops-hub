"""CLI skeleton for codex-hermes-supervisor."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from codex_hermes_supervisor.core import paths
from codex_hermes_supervisor.core.config import SupervisorConfig, config_to_dict, load_config, save_config, update_config_value
from codex_hermes_supervisor.core.identity import normalize_windows_path
from codex_hermes_supervisor.integrations.hermes import (
    HermesDirectModeError,
    build_runtime_report,
    import_outbox_note_to_memory,
    run_runtime_selftest,
    sync_outbox_to_direct_memory,
)
from codex_hermes_supervisor.integrations.obsidian import initialize_obsidian_vault
from codex_hermes_supervisor.integrations.qmd import build_qmd_doctor_report, sync_qmd_collections
from codex_hermes_supervisor.services.agent_compat import build_codex_compat_report, lint_agent_instruction_rules, repair_managed_agents
from codex_hermes_supervisor.services.doctor import build_doctor_report
from codex_hermes_supervisor.services.dry_run import run_dry_run
from codex_hermes_supervisor.services.install_global import apply_install_global, get_managed_agent_templates, rollback_install_global
from codex_hermes_supervisor.services.install_project import install_project_config, project_config_preview
from codex_hermes_supervisor.services.install_preview import build_install_preview
from codex_hermes_supervisor.services.lock_repair import repair_stale_locks
from codex_hermes_supervisor.services.outbox import archive_imported_outbox, list_outbox, mark_outbox_imported
from codex_hermes_supervisor.services.project_git import ProjectGitBootstrapError, ensure_project_git
from codex_hermes_supervisor.services.project_memory import (
    list_projects,
    memory_commit,
    memory_forget,
    memory_import_apply,
    memory_import_preview,
    memory_lookup,
    project_memory_bootstrap,
    memory_refresh,
    memory_review,
    memory_search,
    memory_status,
    reindex_vector_local,
    register_project,
    show_project,
    vector_index_status,
)
from codex_hermes_supervisor.services.qmd_eval import evaluate_qmd_fixture, evaluate_qmd_gates
from codex_hermes_supervisor.services.search_eval import evaluate_search_fixture, evaluate_search_gates
from codex_hermes_supervisor.mcp_server import main as mcp_main

app = typer.Typer(help="Codex-Hermes supervisor CLI.")


@app.command()
def doctor(
    repair_locks: bool = typer.Option(False, "--repair-locks"),
    force: bool = typer.Option(False, "--force"),
    codex_compat: bool = typer.Option(False, "--codex-compat"),
) -> None:
    """Environment and contract validation placeholder."""
    config = load_config()
    report = build_doctor_report(config, codex_compat=codex_compat)
    payload = report.model_dump()
    if repair_locks:
        payload["lock_repair"] = repair_stale_locks(config, force=force).model_dump()
    if codex_compat:
        payload["codex_compat"] = build_codex_compat_report(templates=get_managed_agent_templates()).model_dump()
    typer.echo(json.dumps(payload, indent=2))
    raise typer.Exit(code=0)


@app.command(name="config-show")
def config_show() -> None:
    """Show the current supervisor config."""
    typer.echo(json.dumps(config_to_dict(load_config()), indent=2))


@app.command(name="config-set")
def config_set(key: str = typer.Argument(...), value: str = typer.Argument(...)) -> None:
    """Set one config value using dotted-path syntax."""
    config = load_config()
    lowered = value.lower()
    if lowered in {"true", "false"}:
        parsed: object = lowered == "true"
    else:
        try:
            parsed = int(value)
        except ValueError:
            parsed = value
    updated = update_config_value(config, key, parsed)
    path = save_config(updated)
    typer.echo(json.dumps({"config_path": normalize_windows_path(path), "key": key, "value": parsed}, indent=2))


@app.command(name="install-global")
def install_global(
    dry_run: bool = typer.Option(False, "--dry-run"),
    rollback: str | None = typer.Option(None, "--rollback"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Installer placeholder."""
    config = load_config()
    if rollback:
        restored = rollback_install_global(rollback)
        typer.echo(json.dumps({"rollback": rollback, "restored_files": [str(path) for path in restored]}, indent=2))
        raise typer.Exit(code=0)
    if dry_run:
        try:
            preview = build_install_preview(config)
        except ValueError as exc:
            typer.echo(json.dumps({"ok": False, "code": "AGENT_TEMPLATE_INVALID", "message": str(exc)}, indent=2))
            raise typer.Exit(code=2)
        typer.echo(json.dumps(preview.model_dump(), indent=2))
        raise typer.Exit(code=0)
    try:
        result = apply_install_global(config, force=force)
    except ValueError as exc:
        typer.echo(json.dumps({"ok": False, "code": "AGENT_TEMPLATE_INVALID", "message": str(exc)}, indent=2))
        raise typer.Exit(code=2)
    typer.echo(
        json.dumps(
            {
                "backup_dir": str(result.backup_dir),
                "modified_files": [str(path) for path in result.modified_files],
                "selected_skill_root": str(result.selected_skill_root),
                "agent_root": str(result.agent_root),
            },
            indent=2,
        )
    )
    raise typer.Exit(code=0)


@app.command(name="repair-agents")
def repair_agents(dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Repair harness-managed custom agent TOML files after Codex schema changes."""
    report = repair_managed_agents(templates=get_managed_agent_templates(), dry_run=dry_run)
    typer.echo(json.dumps(report.model_dump(), indent=2))
    raise typer.Exit(code=0)


@app.command(name="agents-lint")
def agents_lint(repo: str = typer.Option(..., "--repo")) -> None:
    """Check project AGENTS files for weakening of global Codex-Hermes rules."""
    report = lint_agent_instruction_rules(Path(repo))
    typer.echo(json.dumps(report.model_dump(), indent=2))
    raise typer.Exit(code=0 if report.ok else 2)


@app.command(name="install-project")
def install_project(repo: str = typer.Option(..., "--repo"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Install a project-scoped Codex MCP config into <repo>\\.codex\\config.toml."""
    repo_root = Path(repo)
    if dry_run:
        typer.echo(json.dumps(project_config_preview(repo_root), indent=2))
        raise typer.Exit(code=0)
    result = install_project_config(repo_root, dry_run=False)
    typer.echo(
        json.dumps(
            {
                "config_path": normalize_windows_path(result.config_path),
                "agents_path": normalize_windows_path(result.agents_path),
                "template_root": normalize_windows_path(result.template_root),
                "changed": result.changed,
                "changed_paths": [normalize_windows_path(path) for path in result.changed_paths],
            },
            indent=2,
        )
    )
    raise typer.Exit(code=0)


@app.command()
def outbox_list() -> None:
    """List Hermes outbox files."""
    config = load_config()
    typer.echo(json.dumps(list_outbox(config.hermes_outbox_root).model_dump(), indent=2))


@app.command(name="outbox-mark-imported")
def outbox_mark_imported(path: str) -> None:
    """Mark one outbox file as imported."""
    updated = mark_outbox_imported(Path(path))
    typer.echo(json.dumps({"updated": str(updated)}, indent=2))


@app.command(name="outbox-archive-imported")
def outbox_archive_imported() -> None:
    """Archive imported outbox files."""
    config = load_config()
    archived = archive_imported_outbox(config.hermes_outbox_root)
    typer.echo(json.dumps({"archived": [str(path) for path in archived]}, indent=2))


@app.command(name="outbox-import-memory")
def outbox_import_memory(path: str, profile: str = typer.Option("coder", "--profile"), target: str = typer.Option("memory", "--target")) -> None:
    """Import one outbox note into Hermes built-in file memory."""
    imported = import_outbox_note_to_memory(Path(path), profile=profile, target=target)
    typer.echo(json.dumps({"imported_path": normalize_windows_path(imported), "profile": profile, "target": target}, indent=2))


@app.command(name="hermes-doctor")
def hermes_doctor() -> None:
    """Show Hermes runtime availability and fallback mode."""
    config = load_config()
    typer.echo(json.dumps(build_runtime_report(config).model_dump(), indent=2))


@app.command(name="hermes-selftest")
def hermes_selftest(
    backend: str = typer.Option("configured", "--backend"),
    target: str = typer.Option("memory", "--target"),
    write_probe: bool = typer.Option(False, "--write-probe"),
) -> None:
    """Exercise the configured Hermes direct runtime end to end."""
    config = load_config()
    try:
        report = run_runtime_selftest(config, backend=backend, target=target, write_probe=write_probe)
    except HermesDirectModeError as exc:
        typer.echo(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, indent=2))
        raise typer.Exit(code=2)
    typer.echo(json.dumps(report.model_dump(), indent=2))


@app.command(name="hermes-sync-outbox")
def hermes_sync_outbox(
    target: str = typer.Option("memory", "--target"),
    archive: bool = typer.Option(False, "--archive"),
    backend: str = typer.Option("configured", "--backend"),
) -> None:
    """Import pending outbox notes into the configured direct Hermes mode."""
    config = load_config()
    if backend != "configured":
        payload = config.model_dump(mode="python")
        hermes_payload = dict(payload["hermes"])
        hermes_payload["mode"] = backend
        payload["hermes"] = hermes_payload
        config = SupervisorConfig.model_validate(payload)
    try:
        imported = sync_outbox_to_direct_memory(config, target=target, archive=archive)
    except HermesDirectModeError as exc:
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "code": exc.code,
                    "message": exc.message,
                },
                indent=2,
            )
        )
        raise typer.Exit(code=2)
    typer.echo(json.dumps({"imported": [normalize_windows_path(path) for path in imported], "archive": archive}, indent=2))


@app.command(name="dry-run")
def dry_run_command() -> None:
    """Run an end-to-end smoke flow on a temporary git repo."""
    config = load_config()
    report = run_dry_run(config)
    typer.echo(json.dumps(report.model_dump(), indent=2))


@app.command(name="project-register")
def project_register(repo: str = typer.Option(..., "--repo"), name: str | None = typer.Option(None, "--name")) -> None:
    """Register a project for project-memory onboarding."""
    result = register_project(Path(repo), name=name)
    typer.echo(json.dumps(result.model_dump(), indent=2))


@app.command(name="project-git-ensure")
def project_git_ensure(
    repo: str = typer.Option(..., "--repo"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_initial_commit: bool = typer.Option(False, "--no-initial-commit"),
) -> None:
    """Ensure a project folder has a safe Git baseline for Supervisor guardrails."""
    try:
        result = ensure_project_git(Path(repo), dry_run=dry_run, create_initial_commit=not no_initial_commit)
    except ProjectGitBootstrapError as exc:
        typer.echo(json.dumps({"ok": False, "code": "PROJECT_GIT_BOOTSTRAP_FAILED", "message": str(exc)}, indent=2))
        raise typer.Exit(code=2)
    typer.echo(json.dumps({"ok": True, **result.to_dict()}, indent=2))


@app.command(name="project-memory-bootstrap")
def project_memory_bootstrap_command(
    repo: str = typer.Option(..., "--repo"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Create project-scoped Obsidian CodexWiki status/log/source entrypoints."""
    config = load_config()
    result = project_memory_bootstrap(config, Path(repo), dry_run=dry_run)
    typer.echo(json.dumps(result.model_dump(), indent=2))


@app.command(name="project-list")
def project_list() -> None:
    """List registered projects."""
    typer.echo(json.dumps(list_projects().model_dump(), indent=2))


@app.command(name="project-show")
def project_show(project_id: str = typer.Option(..., "--project-id")) -> None:
    """Show one registered project entry."""
    typer.echo(json.dumps(show_project(project_id).model_dump(), indent=2))


@app.command(name="memory-import")
def memory_import(
    repo: str = typer.Option(..., "--repo"),
    mode: str = typer.Option("baseline", "--mode"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Import existing project memory into the supervisor onboarding structure."""
    config = load_config()
    if dry_run:
        preview = memory_import_preview(config, Path(repo), mode=mode)
        typer.echo(json.dumps(preview.model_dump(), indent=2))
        raise typer.Exit(code=0)
    result = memory_import_apply(config, Path(repo), mode=mode)
    typer.echo(json.dumps(result.model_dump(), indent=2))


@app.command(name="memory-review")
def memory_review_command(project_id: str = typer.Option(..., "--project-id")) -> None:
    """Review the current imported memory state for a project."""
    typer.echo(json.dumps(memory_review(project_id).model_dump(), indent=2))


@app.command(name="memory-commit")
def memory_commit_command(project_id: str = typer.Option(..., "--project-id")) -> None:
    """Commit a reviewed compact summary to Hermes outbox."""
    config = load_config()
    typer.echo(json.dumps(memory_commit(config, project_id).model_dump(), indent=2))


@app.command(name="memory-status")
def memory_status_command(project_id: str = typer.Option(..., "--project-id")) -> None:
    """Show project memory onboarding status."""
    typer.echo(json.dumps(memory_status(project_id).model_dump(), indent=2))


@app.command(name="memory-refresh")
def memory_refresh_command(repo: str = typer.Option(..., "--repo"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Refresh source-hash staleness against the saved memory manifest."""
    config = load_config()
    typer.echo(json.dumps(memory_refresh(config, Path(repo), dry_run=dry_run).model_dump(), indent=2))


@app.command(name="memory-forget")
def memory_forget_command(project_id: str = typer.Option(..., "--project-id")) -> None:
    """Mark a project's imported memory as rejected."""
    typer.echo(json.dumps(memory_forget(project_id).model_dump(), indent=2))


@app.command(name="memory-search")
def memory_search_command(
    query: str = typer.Argument(...),
    project_id: str | None = typer.Option(None, "--project-id"),
    limit: int = typer.Option(10, "--limit"),
    mode: str = typer.Option("keyword", "--mode"),
    backend: str | None = typer.Option(None, "--backend"),
) -> None:
    """Search imported project memory manifests, notes, and outbox files."""
    config = load_config()
    typer.echo(json.dumps(memory_search(query, project_id=project_id, limit=limit, mode=mode, backend=backend, config=config).model_dump(), indent=2))


@app.command(name="memory-lookup")
def memory_lookup_command(
    query: str = typer.Argument(...),
    repo: str = typer.Option(..., "--repo"),
    project_id: str | None = typer.Option(None, "--project-id"),
    workstream_id: str | None = typer.Option(None, "--workstream-id"),
    limit: int = typer.Option(8, "--limit"),
    mode: str = typer.Option("hybrid", "--mode"),
    backend: str | None = typer.Option(None, "--backend"),
    timeout_seconds: float | None = typer.Option(55.0, "--timeout-seconds"),
) -> None:
    """Build a scoped LLM Wiki context pack from user keywords."""
    config = load_config()
    result = memory_lookup(
        query,
        repo_root=Path(repo),
        config=config,
        project_id=project_id,
        workstream_id=workstream_id,
        limit=limit,
        mode=mode,
        backend=backend,
        timeout_seconds=timeout_seconds,
    )
    typer.echo(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))


@app.command(name="qmd-doctor")
def qmd_doctor() -> None:
    """Show QMD backend availability and configured collection roots."""
    config = load_config()
    typer.echo(json.dumps(build_qmd_doctor_report(config).model_dump(), indent=2))


@app.command(name="qmd-sync")
def qmd_sync(embed: bool = typer.Option(False, "--embed"), force_embed: bool = typer.Option(False, "--force-embed")) -> None:
    """Register configured QMD collections and optionally run embeddings."""
    config = load_config()
    typer.echo(json.dumps(sync_qmd_collections(config, embed=embed, force_embed=force_embed).model_dump(), indent=2))


@app.command(name="search-eval")
def search_eval(
    fixture: str = typer.Option(..., "--fixture"),
    min_top1_rate: float | None = typer.Option(None, "--min-top1-rate"),
    min_topk_rate: float | None = typer.Option(None, "--min-topk-rate"),
    allow_forbidden_hits: bool = typer.Option(True, "--allow-forbidden-hits/--fail-on-forbidden-hits"),
    require_backend_match: bool = typer.Option(False, "--require-backend-match"),
) -> None:
    """Run a generalized search-evaluation fixture across one or more backends and modes."""
    config = load_config()
    report = evaluate_search_fixture(config, Path(fixture))
    gate = evaluate_search_gates(
        report,
        min_top1_rate=min_top1_rate,
        min_topk_rate=min_topk_rate,
        allow_forbidden_hits=allow_forbidden_hits,
        require_backend_match=require_backend_match,
    )
    typer.echo(json.dumps({"report": report.model_dump(), "gate": gate.model_dump()}, indent=2))
    raise typer.Exit(code=0 if gate.ok else 2)


@app.command(name="qmd-eval")
def qmd_eval(
    fixture: str = typer.Option(..., "--fixture"),
    min_top1_rate: float | None = typer.Option(None, "--min-top1-rate"),
    min_topk_rate: float | None = typer.Option(None, "--min-topk-rate"),
    allow_forbidden_hits: bool = typer.Option(True, "--allow-forbidden-hits/--fail-on-forbidden-hits"),
    require_backend_match: bool = typer.Option(False, "--require-backend-match"),
) -> None:
    """Run a QMD quality-evaluation fixture against the current runtime."""
    config = load_config()
    report = evaluate_qmd_fixture(config, Path(fixture))
    gate = evaluate_qmd_gates(
        report,
        min_top1_rate=min_top1_rate,
        min_topk_rate=min_topk_rate,
        allow_forbidden_hits=allow_forbidden_hits,
        require_backend_match=require_backend_match,
    )
    typer.echo(json.dumps({"report": report.model_dump(), "gate": gate.model_dump()}, indent=2))
    raise typer.Exit(code=0 if gate.ok else 2)


@app.command(name="vector-doctor")
def vector_doctor(project_id: str | None = typer.Option(None, "--project-id")) -> None:
    """Show local vector index availability and status."""
    config = load_config()
    typer.echo(json.dumps(vector_index_status(config, project_id=project_id).model_dump(), indent=2))


@app.command(name="vector-reindex")
def vector_reindex(project_id: str | None = typer.Option(None, "--project-id")) -> None:
    """Build or rebuild the persisted local vector index."""
    config = load_config()
    typer.echo(json.dumps(reindex_vector_local(config, project_id=project_id).model_dump(), indent=2))

@app.command(name="obsidian-init")
def obsidian_init(
    vault_root: str | None = typer.Option(None, "--vault-root"),
    wiki_root: str | None = typer.Option(None, "--wiki-root"),
) -> None:
    """Enable Obsidian integration and initialize the CodexWiki vault structure."""
    config = load_config()
    root = Path(vault_root).expanduser().resolve() if vault_root else (paths.user_home() / "ObsidianVault").resolve()
    if wiki_root:
        config.obsidian.wiki_root = wiki_root
    config.obsidian.enabled = True
    config.obsidian.vault_root = str(root)
    wiki_dir = initialize_obsidian_vault(root, config.obsidian.wiki_root)
    config_path = save_config(config)
    typer.echo(
        json.dumps(
            {
                "config_path": normalize_windows_path(config_path),
                "vault_root": normalize_windows_path(root),
                "wiki_root": config.obsidian.wiki_root,
                "wiki_dir": normalize_windows_path(wiki_dir),
                "enabled": True,
            },
            indent=2,
        )
    )


@app.command()
def mcp() -> None:
    """Run the real MCP stdio server."""
    raise typer.Exit(code=mcp_main(["--profile", "coder"]))


def main() -> None:
    """Module entrypoint for `py -m codex_hermes_supervisor.cli`."""

    app()


if __name__ == "__main__":
    main()
