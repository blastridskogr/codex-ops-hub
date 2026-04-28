from __future__ import annotations

import subprocess
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import build_identity
from codex_hermes_supervisor.integrations.hermes import write_outbox_note
from codex_hermes_supervisor.services.project_memory import memory_commit, memory_import_apply, register_project
from codex_hermes_supervisor.services.harness import (
    harness_begin,
    harness_checkpoint_tool,
    harness_check,
    harness_finish_tool,
    harness_plan,
    version_prepare_tool,
    version_sync_tool,
)
from codex_hermes_supervisor.schemas.project_memory import MemoryPreflightResult
from codex_hermes_supervisor.schemas.wiki import WikiNoteData
from codex_hermes_supervisor.schemas.tools import (
    HarnessBeginInput,
    HarnessCheckpointInput,
    HarnessCheckInput,
    HarnessFinishInput,
    HarnessPlanInput,
    VersionPrepareInput,
    VersionSyncInput,
)


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    readme = repo / "README.md"
    readme.write_text("# test\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-m", "init"], repo)
    return repo


def _config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig.model_validate(
        {
            "state": {
                "root": str(tmp_path / "state"),
                "lock_timeout_seconds": 30,
                "heartbeat_seconds": 1,
            },
            "hermes": {
                "mode": "outbox",
                "outbox_dir": str(tmp_path / "outbox"),
            },
            "obsidian": {
                "enabled": False,
                "vault_root": "",
            },
        }
    )


def test_harness_plan_requires_memory_preflight_when_enabled(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.memory_policy.require_memory_preflight_for_plan = True

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id

    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/TEST.py"],
            risk_level="low",
        ),
    )

    assert plan.ok is False
    assert plan.errors
    assert plan.errors[0].code == "MEMORY_PREFLIGHT_REQUIRED"


def test_harness_plan_records_memory_preflight_context(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id
    identity = build_identity(repo.resolve())
    preflight = MemoryPreflightResult(
        query="create script",
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=str(repo.resolve()),
        memory_decision="targeted_lookup",
        lookup_required=True,
        lookup_ran=True,
        memory_evidence_ready=True,
        source_paths=["C:/vault/CodexWiki/Projects/project/status.md"],
        rejected_reference_paths=["C:/vault/CodexWiki/Sources/other/reference.md"],
        workstream_id="test-workstream",
        warnings=["REFERENCE_ONLY_HIT_REJECTED"],
    )

    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/TEST.py"],
            risk_level="low",
            require_memory_preflight=True,
            memory_preflight=preflight,
        ),
    )

    assert plan.ok is True
    context = plan.data.plan.memory_context
    assert context.preflight_required is True
    assert context.memory_decision == "targeted_lookup"
    assert context.lookup_ran is True
    assert context.memory_evidence_ready is True
    assert context.source_paths == ["C:/vault/CodexWiki/Projects/project/status.md"]
    assert context.rejected_reference_paths == ["C:/vault/CodexWiki/Sources/other/reference.md"]
    assert context.workstream_id == "test-workstream"


def test_harness_finish_blocks_missing_required_verification(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    assert begin.ok is True
    task_id = begin.data.task_id

    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/TEST.py"],
            managed_files=[{"active_path": "tools/TEST.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="medium",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    assert plan.ok is True

    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/TEST.py",
            reason="create script",
        ),
    )
    version_path = Path(prepared.data.version_path)
    version_path.write_text("print('hello')\n", encoding="utf-8")
    synced = version_sync_tool(
        config,
        VersionSyncInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/TEST.py",
            version_path=prepared.data.version_path,
        ),
    )
    assert synced.ok is True

    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.ok is True
    assert checked.data.check_passed is True

    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
        ),
    )
    assert finished.ok is True
    assert any(item.type == "required_verification_missing" for item in finished.data.violations)


def test_harness_flow_end_to_end_with_manual_review(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    assert begin.ok is True
    task_id = begin.data.task_id

    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/TEST.py"],
            managed_files=[{"active_path": "tools/TEST.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="low",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    assert plan.ok is True

    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/TEST.py",
            reason="create script",
        ),
    )
    version_path = Path(prepared.data.version_path)
    version_path.write_text("print('hello')\n", encoding="utf-8")
    synced = version_sync_tool(
        config,
        VersionSyncInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/TEST.py",
            version_path=prepared.data.version_path,
        ),
    )
    assert synced.ok is True

    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.data.check_passed is True

    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
            verification_results=[
                {
                    "kind": "manual_review",
                    "status": "passed",
                    "evidence": "Reviewed final diff.",
                    "satisfies_step": 0,
                }
            ],
        ),
    )
    assert finished.ok is True
    assert finished.data.phase == "FINISHED"
    assert Path(finished.data.worklog_path).exists()
    assert finished.data.handoff is not None
    handoff_path = Path(finished.data.handoff.path)
    assert handoff_path.exists()
    assert finished.data.handoff.requested_mode == "outbox"
    assert finished.data.handoff.actual_mode == "outbox"
    assert finished.data.handoff.fallback is False
    identity = build_identity(repo)
    assert handoff_path.parent == config.hermes_outbox_root / "projects" / identity.project_id / "handoff"
    handoff_text = handoff_path.read_text(encoding="utf-8")
    assert "outbox_schema_version: 2" in handoff_text
    assert "scope: project" in handoff_text
    assert f"project_id: {identity.project_id}" in handoff_text
    assert f"workspace_id: {identity.workspace_id}" in handoff_text
    assert "memory_kind: handoff" in handoff_text
    assert f"source_task_id: {task_id}" in handoff_text
    assert "source_tool: harness_finish" in handoff_text


def test_harness_begin_returns_compact_hermes_recall_from_local_state(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    identity = build_identity(repo)
    workspace_id = identity.workspace_id
    tasks_dir = config.state_root / workspace_id / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "lessons.md").write_text("Inspect targeted tests before asking for clarification.\n", encoding="utf-8")
    write_outbox_note(
        config.hermes_outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id=identity.project_id,
        workspace_id=workspace_id,
        title="Handoff",
        compact_summary="Recent handoff summary.",
    )

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    assert begin.ok is True
    assert begin.data is not None
    assert begin.data.hermes.recall_available is True
    assert "Local lessons" in begin.data.hermes.recall
    assert "Recent handoffs" in begin.data.hermes.recall
    assert begin.data.hermes.recall_filter is not None
    assert begin.data.hermes.recall_filter.current_project_id == identity.project_id
    state = config.state_root / workspace_id / "state.json"
    assert '"hermes_recall_done": true' in state.read_text(encoding="utf-8").lower()


def test_harness_begin_bootstraps_non_git_project(tmp_path: Path) -> None:
    repo = tmp_path / "plain-project"
    repo.mkdir()
    (repo / "payload.txt").write_text("user data\n", encoding="utf-8")
    config = _config(tmp_path)

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="inspect project"))

    assert begin.ok is True
    assert begin.data is not None
    assert begin.data.identity.repo_root == str(repo.resolve()).replace("/", "\\")
    assert begin.data.baseline.head
    assert "payload.txt" in begin.data.baseline.untracked_files_at_start
    tracked = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    assert tracked == [".gitignore"]


def test_harness_checkpoint_records_structured_worklog_entry(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="checkpoint flow"))
    assert begin.ok is True
    task_id = begin.data.task_id

    checkpoint = harness_checkpoint_tool(
        config,
        HarnessCheckpointInput(
            repo_root=str(repo),
            task_id=task_id,
            kind="failed_attempt",
            summary="Initial probe failed, continuing with a safer alternate path.",
            evidence=["probe.log showed missing optional input"],
            next_action="Run a narrower local check.",
        ),
    )

    assert checkpoint.ok is True
    assert checkpoint.data.recorded is True
    worklog = Path(checkpoint.data.worklog_path).read_text(encoding="utf-8")
    assert "### Checkpoint: failed_attempt" in worklog
    assert "Initial probe failed" in worklog
    assert "probe.log showed missing optional input" in worklog
    assert "Next action: Run a narrower local check." in worklog


def test_harness_begin_includes_reviewed_project_context(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder"},
            "obsidian": {"enabled": True, "vault_root": str(tmp_path / "vault"), "wiki_root": "CodexWiki"},
        }
    )

    register_project(repo, name="repo")
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    assert begin.ok is True
    assert begin.data is not None
    assert "Project baseline:" in begin.data.hermes.recall
    assert "[[CodexWiki/Projects/repo" in begin.data.hermes.recall


def test_harness_begin_reads_builtin_memory_in_direct_mode(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    memory_root = home / ".hermes" / "memories"
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "MEMORY.md").write_text("Direct Hermes memory entry.\n", encoding="utf-8")
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "python_library", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder", "python_module": "missing_hermes_module"},
            "obsidian": {"enabled": False, "vault_root": ""},
        }
    )

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="direct recall"))
    assert begin.ok is True
    assert begin.data is not None
    assert "Hermes builtin files" in begin.data.hermes.recall
    assert "Direct Hermes memory entry." in begin.data.hermes.recall
    assert begin.data.hermes.requested_mode == "python_library"
    assert begin.data.hermes.actual_mode == "builtin_file_fallback"
    assert begin.data.hermes.fallback is True
    assert begin.data.hermes.sources
    assert any(source.source_type == "builtin_file_fallback" for source in begin.data.hermes.sources)


def test_harness_finish_reports_actual_handoff_mode_for_cli_fallback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "cli", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder", "cli_executable": "missing-hermes-cli"},
            "obsidian": {"enabled": False, "vault_root": ""},
        }
    )

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id
    harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/CLI.py"],
            managed_files=[{"active_path": "tools/CLI.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="low",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(repo_root=str(repo), task_id=task_id, active_path="tools/CLI.py", reason="create script"),
    )
    Path(prepared.data.version_path).write_text("print('cli')\n", encoding="utf-8")
    version_sync_tool(
        config,
        VersionSyncInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/CLI.py",
            version_path=prepared.data.version_path,
        ),
    )
    harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
            verification_results=[{"kind": "manual_review", "status": "passed", "evidence": "Reviewed final diff.", "satisfies_step": 0}],
        ),
    )
    assert finished.ok is True
    assert finished.data.handoff.requested_mode == "cli"
    assert finished.data.handoff.actual_mode == "builtin_file_fallback"
    assert finished.data.handoff.mode == "builtin_file_fallback"
    assert finished.data.handoff.fallback is True
    assert finished.data.handoff.fallback_reason is not None


def test_harness_finish_reports_builtin_file_direct_mode(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "builtin_file", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder"},
            "obsidian": {"enabled": False, "vault_root": ""},
        }
    )

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id
    harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/BUILTIN.py"],
            managed_files=[{"active_path": "tools/BUILTIN.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="low",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(repo_root=str(repo), task_id=task_id, active_path="tools/BUILTIN.py", reason="create script"),
    )
    Path(prepared.data.version_path).write_text("print('builtin')\n", encoding="utf-8")
    version_sync_tool(
        config,
        VersionSyncInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/BUILTIN.py",
            version_path=prepared.data.version_path,
        ),
    )
    harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
            verification_results=[{"kind": "manual_review", "status": "passed", "evidence": "Reviewed final diff.", "satisfies_step": 0}],
        ),
    )
    assert finished.ok is True
    assert finished.data.handoff.requested_mode == "builtin_file"
    assert finished.data.handoff.actual_mode == "builtin_file"
    assert finished.data.handoff.mode == "builtin_file"
    assert finished.data.handoff.fallback is False


def test_harness_finish_records_wiki_link_in_state(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {
                "root": str(tmp_path / "state"),
                "lock_timeout_seconds": 30,
                "heartbeat_seconds": 1,
            },
            "hermes": {
                "mode": "outbox",
                "outbox_dir": str(tmp_path / "outbox"),
            },
            "obsidian": {
                "enabled": True,
                "vault_root": str(tmp_path / "vault"),
                "wiki_root": "CodexWiki",
            },
        }
    )

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    assert begin.ok is True
    task_id = begin.data.task_id

    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/NOTE.py"],
            managed_files=[{"active_path": "tools/NOTE.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="low",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    assert plan.ok is True

    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/NOTE.py",
            reason="create script",
        ),
    )
    version_path = Path(prepared.data.version_path)
    version_path.write_text("print('note')\n", encoding="utf-8")
    synced = version_sync_tool(
        config,
        VersionSyncInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/NOTE.py",
            version_path=prepared.data.version_path,
        ),
    )
    assert synced.ok is True

    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.data.check_passed is True

    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
            create_wiki_note=True,
            verification_results=[{"kind": "manual_review", "status": "passed", "evidence": "Reviewed final diff.", "satisfies_step": 0}],
        ),
    )
    assert finished.ok is True
    assert finished.data is not None
    assert finished.data.wiki_note is not None
    assert finished.data.wiki_note.wikilink in finished.data.state.wiki_notes


def test_harness_finish_continues_when_optional_wiki_note_fails(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox")},
            "obsidian": {"enabled": True, "vault_root": str(tmp_path / "vault"), "wiki_root": "CodexWiki"},
        }
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/NOTE.py"],
            managed_files=[{"active_path": "tools/NOTE.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="low",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    assert plan.ok is True
    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(repo_root=str(repo), task_id=task_id, active_path="tools/NOTE.py", reason="create script"),
    )
    Path(prepared.data.version_path).write_text("print('note')\n", encoding="utf-8")
    synced = version_sync_tool(
        config,
        VersionSyncInput(repo_root=str(repo), task_id=task_id, active_path="tools/NOTE.py", version_path=prepared.data.version_path),
    )
    assert synced.ok is True
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.harness.write_wiki_note",
        lambda *args, **kwargs: WikiNoteData(created=False, path="bad.md", message="boom", warning_code="WIKI_NOTE_WRITE_FAILED"),
    )
    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
            create_wiki_note=True,
            verification_results=[{"kind": "manual_review", "status": "passed", "evidence": "Reviewed final diff.", "satisfies_step": 0}],
        ),
    )
    assert finished.ok is True
    assert finished.data is not None
    assert finished.data.phase == "FINISHED"
    assert finished.data.wiki_note is not None
    assert finished.data.wiki_note.created is False


def test_harness_finish_blocks_when_required_wiki_note_fails(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox")},
            "obsidian": {"enabled": True, "vault_root": str(tmp_path / "vault"), "wiki_root": "CodexWiki"},
        }
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Create script",
            allowed_files=["tools/NOTE.py"],
            managed_files=[{"active_path": "tools/NOTE.py", "reason": "generated script", "versioning_mode": "side_by_side"}],
            risk_level="low",
            verification_steps=[{"kind": "manual_review", "description": "Review diff", "required": True, "reason": "Required review"}],
        ),
    )
    assert plan.ok is True
    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(repo_root=str(repo), task_id=task_id, active_path="tools/NOTE.py", reason="create script"),
    )
    Path(prepared.data.version_path).write_text("print('note')\n", encoding="utf-8")
    synced = version_sync_tool(
        config,
        VersionSyncInput(repo_root=str(repo), task_id=task_id, active_path="tools/NOTE.py", version_path=prepared.data.version_path),
    )
    assert synced.ok is True
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.harness.write_wiki_note",
        lambda *args, **kwargs: WikiNoteData(created=False, path="bad.md", message="boom", warning_code="WIKI_NOTE_WRITE_FAILED"),
    )
    finished = harness_finish_tool(
        config,
        HarnessFinishInput(
            repo_root=str(repo),
            task_id=task_id,
            summary="Created script",
            create_wiki_note=True,
            require_wiki_note=True,
            verification_results=[{"kind": "manual_review", "status": "passed", "evidence": "Reviewed final diff.", "satisfies_step": 0}],
        ),
    )
    assert finished.ok is False
    assert finished.errors
    assert finished.errors[0].code == "WIKI_NOTE_WRITE_FAILED"


def test_harness_check_preserves_preexisting_untracked_paths(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
            "hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox")},
            "obsidian": {"enabled": False, "vault_root": ""},
            "git_guard": {"include_untracked": True, "include_staged": True},
        }
    )

    preexisting = repo / "notes"
    preexisting.mkdir()
    (preexisting / "old.txt").write_text("keep me\n", encoding="utf-8")

    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create script"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Allow only tools/SAFE.py",
            allowed_files=["tools/SAFE.py"],
            risk_level="low",
        ),
    )
    assert plan.ok is True

    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.ok is True
    assert checked.data.check_passed is True
    assert not any(item.path == "notes" for item in checked.data.changed_files)
    assert (preexisting / "old.txt").exists()
