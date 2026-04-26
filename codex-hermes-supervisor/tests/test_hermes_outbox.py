from __future__ import annotations

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.integrations.hermes import (
    HermesDirectModeError,
    append_direct_memory,
    build_recall_bundle,
    build_recall_context,
    build_runtime_report,
    run_runtime_selftest,
    sync_outbox_to_direct_memory,
    write_outbox_note,
)


def test_write_outbox_note_deduplicates_same_content(tmp_path) -> None:
    outbox_root = tmp_path / "hermes_outbox"
    first_path, first_hash = write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="Compact summary",
    )
    second_path, second_hash = write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="Compact summary",
    )
    assert first_path == second_path
    assert first_hash == second_hash
    assert "projects" in first_path.parts
    assert "project-1" in first_path.parts
    assert len(list((outbox_root / "projects" / "project-1" / "handoff").glob("*.md"))) == 1
    note_text = first_path.read_text(encoding="utf-8")
    assert "outbox_schema_version: 2" in note_text
    assert "scope: project" in note_text
    assert "memory_kind: handoff" in note_text
    assert "source_tool: manual" in note_text


def test_build_recall_context_uses_outbox_and_lessons(tmp_path) -> None:
    outbox_root = tmp_path / "hermes_outbox"
    tasks_dir = tmp_path / "state" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "lessons.md").write_text("Use targeted tests first.\nAvoid lockfile edits.\n", encoding="utf-8")
    write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="Created TEST.py through the supervisor flow.",
    )

    recall = build_recall_context(outbox_root, tasks_dir, profile="coder", read_builtin_files=False)
    assert "Local lessons" in recall
    assert "Use targeted tests first." in recall
    assert "Recent handoffs" in recall
    assert "Created TEST.py through the supervisor flow." in recall


def test_build_recall_bundle_filters_cross_project_and_legacy_outbox(tmp_path) -> None:
    outbox_root = tmp_path / "hermes_outbox"
    tasks_dir = tmp_path / "state" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    config = SupervisorConfig.model_validate({"hermes": {"mode": "outbox", "outbox_dir": str(outbox_root)}})

    write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-current",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="Current project handoff.",
    )
    write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-other",
        project_id="project-2",
        workspace_id="workspace-2",
        title="Handoff",
        compact_summary="Other project handoff must not be evidence.",
    )
    write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-global",
        project_id="",
        workspace_id="",
        scope="global",
        memory_kind="global_lesson",
        title="Global Handoff",
        compact_summary="Global handoff is allowed.",
    )
    legacy = outbox_root / "handoff" / "legacy.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "---\n"
        "type: handoff\n"
        "status: pending_import\n"
        "---\n\n"
        "# Legacy\n\n"
        "Legacy unscoped handoff must not be evidence.\n",
        encoding="utf-8",
    )

    bundle = build_recall_bundle(
        config,
        outbox_root,
        tasks_dir,
        current_project_id="project-1",
        current_workspace_id="workspace-1",
    )

    assert "Current project handoff." in bundle.recall
    assert "Global handoff is allowed." in bundle.recall
    assert "Other project handoff must not be evidence." not in bundle.recall
    assert "Legacy unscoped handoff must not be evidence." not in bundle.recall
    assert bundle.recall_filter is not None
    assert bundle.recall_filter.allowed_count == 2
    assert bundle.recall_filter.filtered_cross_project_count == 1
    assert bundle.recall_filter.legacy_unscoped_count == 1


def test_write_outbox_note_partitions_global_and_workspace_scopes(tmp_path) -> None:
    outbox_root = tmp_path / "hermes_outbox"
    global_path, _ = write_outbox_note(
        outbox_root,
        note_type="lesson",
        task_id="task-global",
        project_id="",
        workspace_id="",
        scope="global",
        memory_kind="global_lesson",
        title="Global Lesson",
        compact_summary="Global lesson.",
    )
    workspace_path, _ = write_outbox_note(
        outbox_root,
        note_type="handoff",
        task_id="task-workspace",
        project_id="project-1",
        workspace_id="workspace-1",
        scope="workspace",
        title="Workspace Handoff",
        compact_summary="Workspace handoff.",
    )

    assert global_path.parent == outbox_root / "global" / "lessons"
    assert workspace_path.parent == outbox_root / "workspaces" / "workspace-1" / "handoff"


def test_build_recall_bundle_truncates_builtin_memory(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    outbox_root = tmp_path / "hermes_outbox"
    tasks_dir = tmp_path / "state" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    memory_dir = home / ".hermes" / "memories"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("A" * 5000, encoding="utf-8")
    (memory_dir / "USER.md").write_text("B" * 4000, encoding="utf-8")
    (memory_dir / "SOUL.md").write_text("C" * 3000, encoding="utf-8")

    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "builtin_file",
                "profile": "coder",
                "read_builtin_files": True,
                "builtin_recall_max_total_chars": 1200,
                "builtin_recall_memory_chars": 700,
                "builtin_recall_user_chars": 500,
                "builtin_recall_soul_chars": 400,
            }
        }
    )
    bundle = build_recall_bundle(config, outbox_root, tasks_dir)
    assert len(bundle.recall) <= 1200
    assert bundle.sources
    assert any(source.path and source.path.endswith("MEMORY.md") and source.truncated for source in bundle.sources)
    assert any(source.original_chars is not None and source.included_chars is not None for source in bundle.sources)


def test_build_runtime_report_falls_back_when_cli_missing(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate({"hermes": {"mode": "cli", "profile": "coder", "cli_executable": "missing-hermes-cli"}})
    report = build_runtime_report(config)
    assert report.requested_mode == "cli"
    assert report.effective_mode == "builtin_file_fallback"
    assert report.read_effective_mode == "builtin_file_fallback"
    assert report.warnings


def test_build_runtime_report_for_builtin_file_mode(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate({"hermes": {"mode": "builtin_file", "profile": "coder"}})
    report = build_runtime_report(config)
    assert report.requested_mode == "builtin_file"
    assert report.effective_mode == "builtin_file"
    assert report.warnings == []


def test_build_runtime_report_for_bundled_python_runtime() -> None:
    config = SupervisorConfig.model_validate({"hermes": {"mode": "python_library"}})
    report = build_runtime_report(config)
    assert report.requested_mode == "python_library"
    assert report.effective_mode == "python_library"
    assert report.read_effective_mode == "python_library"
    assert report.runtime_source == "bundled_python"
    assert report.read_runtime_source == "bundled_python"
    assert report.python_module_available is True
    assert report.python_write_ready is True
    assert report.python_read_ready is True


def test_build_runtime_report_for_bundled_cli_runtime() -> None:
    config = SupervisorConfig.model_validate({"hermes": {"mode": "cli"}})
    report = build_runtime_report(config)
    assert report.effective_mode == "cli"
    assert report.read_effective_mode == "cli"
    assert report.runtime_source == "bundled_cli"
    assert report.read_runtime_source == "bundled_cli"


def test_append_direct_memory_uses_bundled_python_runtime(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate({"hermes": {"mode": "python_library", "profile": "coder"}})
    result = append_direct_memory(config, "Bundled python runtime write")
    assert result.actual_mode == "python_library"
    assert "MEMORY.md" in result.path
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    assert "Bundled python runtime write" in memory_file.read_text(encoding="utf-8")


def test_build_recall_bundle_uses_bundled_python_runtime(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    memory_dir = home / ".hermes" / "memories"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("Bundled recall summary", encoding="utf-8")
    config = SupervisorConfig.model_validate({"hermes": {"mode": "python_library", "profile": "coder"}})
    bundle = build_recall_bundle(config, tmp_path / "outbox", tmp_path / "tasks")
    assert bundle.actual_mode == "python_library"
    assert "Bundled recall summary" in bundle.recall


def test_sync_outbox_to_direct_memory_marks_imported(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate(
        {
            "hermes": {"mode": "python_library", "profile": "coder", "outbox_dir": str(tmp_path / "outbox"), "python_module": "missing_hermes_module"},
        }
    )

    note_path, _ = write_outbox_note(
        config.hermes_outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="Direct-memory sync candidate.",
    )
    imported = sync_outbox_to_direct_memory(config)
    assert imported
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    assert memory_file.exists()
    assert "Direct-memory sync candidate." in memory_file.read_text(encoding="utf-8")
    assert "status: imported" in note_path.read_text(encoding="utf-8")


def test_sync_outbox_to_direct_memory_rejects_outbox_mode(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate({"hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder"}})
    try:
        sync_outbox_to_direct_memory(config)
    except HermesDirectModeError as exc:
        assert exc.code == "HERMES_SYNC_REQUIRES_DIRECT_MODE"
    else:
        raise AssertionError("Expected HermesDirectModeError for outbox mode")


def test_sync_outbox_to_direct_memory_builtin_file_mode(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate(
        {
            "hermes": {"mode": "builtin_file", "profile": "coder", "outbox_dir": str(tmp_path / "outbox")},
        }
    )
    write_outbox_note(
        config.hermes_outbox_root,
        note_type="handoff",
        task_id="task-1",
        project_id="project-1",
        workspace_id="workspace-1",
        title="Handoff",
        compact_summary="Builtin-file direct backend sync candidate.",
    )
    imported = sync_outbox_to_direct_memory(config)
    assert imported
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    assert "Builtin-file direct backend sync candidate." in memory_file.read_text(encoding="utf-8")


def test_append_direct_memory_uses_cli_adapter(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    script = tmp_path / "fake_hermes_cli.py"
    output_path = tmp_path / "cli-memory.md"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "if payload['operation'] == 'write':\n"
        f"    print(json.dumps({{'path': r'{output_path}', 'actual_mode': 'cli'}}))\n"
        "else:\n"
        "    print(json.dumps({'recall': 'CLI recall summary', 'sources': [{'source_type': 'cli', 'path': 'cli://memory', 'included_chars': 18, 'original_chars': 18, 'truncated': False, 'status': 'used'}]}))\n",
        encoding="utf-8",
    )
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "cli",
                "profile": "coder",
                "cli_executable": "py",
                "cli_write_args": [str(script)],
                "cli_read_args": [str(script)],
            }
        }
    )
    result = append_direct_memory(config, "CLI direct write")
    assert result.actual_mode == "cli"
    assert result.path == str(output_path)


def test_build_recall_bundle_uses_cli_adapter(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    script = tmp_path / "fake_hermes_cli.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "if payload['operation'] == 'write':\n"
        "    print(json.dumps({'path': 'cli://memory', 'actual_mode': 'cli'}))\n"
        "else:\n"
        "    print(json.dumps({'recall': 'CLI recall summary', 'sources': [{'source_type': 'cli', 'path': 'cli://memory', 'included_chars': 18, 'original_chars': 18, 'truncated': False, 'status': 'used'}]}))\n",
        encoding="utf-8",
    )
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "cli",
                "profile": "coder",
                "cli_executable": "py",
                "cli_write_args": [str(script)],
                "cli_read_args": [str(script)],
            }
        }
    )
    bundle = build_recall_bundle(config, tmp_path / "outbox", tmp_path / "tasks")
    assert bundle.actual_mode == "cli"
    assert bundle.fallback is False
    assert bundle.recall == "CLI recall summary"
    assert bundle.sources[0].source_type == "cli"


def test_append_direct_memory_uses_python_adapter(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "fake_hermes_module.py").write_text(
        "def append_memory(payload):\n"
        "    return {'path': 'py://memory', 'actual_mode': 'python_library'}\n"
        "\n"
        "def build_recall(payload):\n"
        "    return {'recall': 'Python recall summary', 'sources': [{'source_type': 'python_library', 'path': 'py://memory', 'included_chars': 21, 'original_chars': 21, 'truncated': False, 'status': 'used'}]}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_dir))
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "python_library",
                "profile": "coder",
                "python_module": "fake_hermes_module",
                "python_write_symbol": "append_memory",
                "python_read_symbol": "build_recall",
            }
        }
    )
    result = append_direct_memory(config, "python direct write")
    assert result.actual_mode == "python_library"
    assert result.path == "py://memory"
    bundle = build_recall_bundle(config, tmp_path / "outbox", tmp_path / "tasks")
    assert bundle.actual_mode == "python_library"
    assert bundle.recall == "Python recall summary"


def test_build_runtime_report_marks_python_adapter_ready(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "fake_hermes_module.py").write_text(
        "def append_memory(payload):\n"
        "    return {'path': 'py://memory'}\n"
        "\n"
        "def build_recall(payload):\n"
        "    return {'recall': 'Python recall summary'}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_dir))
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "python_library",
                "profile": "coder",
                "python_module": "fake_hermes_module",
                "python_write_symbol": "append_memory",
                "python_read_symbol": "build_recall",
            }
        }
    )
    report = build_runtime_report(config)
    assert report.effective_mode == "python_library"
    assert report.read_effective_mode == "python_library"
    assert report.runtime_source == "external_python"
    assert report.read_runtime_source == "external_python"
    assert report.python_write_ready is True
    assert report.python_read_ready is True


def test_build_runtime_report_warns_for_missing_external_windows_runtime(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "cli",
                "cli_executable": "missing-hermes-cli",
                "cli_write_args": [],
                "cli_read_args": [],
            }
        }
    )
    report = build_runtime_report(config)
    assert report.runtime_source == "fallback_builtin_file"
    assert any("Windows CLI support is not available" in warning for warning in report.warnings)


def test_run_runtime_selftest_uses_bundled_python_runtime(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    config = SupervisorConfig.model_validate({"hermes": {"mode": "python_library", "profile": "coder"}})
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    before = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    report = run_runtime_selftest(config)
    assert report.write_ok is True
    assert report.read_ok is True
    assert report.actual_write_mode == "python_library"
    assert report.actual_read_mode == "python_library"
    assert report.write_probe_performed is False
    assert report.recall_contains_token is False
    after = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    assert after == before


def test_run_runtime_selftest_uses_cli_adapter(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)
    script = tmp_path / "fake_hermes_cli.py"
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    script.write_text(
        "import json, pathlib, sys\n"
        f"memory_file = pathlib.Path(r'{memory_file}')\n"
        "memory_file.parent.mkdir(parents=True, exist_ok=True)\n"
        "payload = json.load(sys.stdin)\n"
        "if payload['operation'] == 'write':\n"
        "    memory_file.write_text(payload['content'], encoding='utf-8')\n"
        "    print(json.dumps({'path': str(memory_file), 'actual_mode': 'cli'}))\n"
        "else:\n"
        "    text = memory_file.read_text(encoding='utf-8') if memory_file.exists() else ''\n"
        "    print(json.dumps({'actual_mode': 'cli', 'recall': text, 'sources': [{'source_type': 'cli', 'path': str(memory_file), 'included_chars': len(text), 'original_chars': len(text), 'truncated': False, 'status': 'used'}]}))\n",
        encoding="utf-8",
    )
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "mode": "cli",
                "profile": "coder",
                "cli_executable": "py",
                "cli_write_args": [str(script)],
                "cli_read_args": [str(script)],
            }
        }
    )
    report = run_runtime_selftest(config, write_probe=True)
    assert report.write_ok is True
    assert report.read_ok is True
    assert report.actual_write_mode == "cli"
    assert report.actual_read_mode == "cli"
    assert report.recall_contains_token is True
    assert report.probe_cleanup_ok is True
