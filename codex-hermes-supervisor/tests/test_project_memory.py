from __future__ import annotations

import subprocess
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.locks import WorkspaceLock
from codex_hermes_supervisor.integrations.hermes import import_outbox_note_to_memory, write_outbox_note
from codex_hermes_supervisor.services.project_memory import (
    list_projects,
    memory_commit,
    memory_import_apply,
    memory_import_preview,
    memory_lookup,
    project_memory_bootstrap,
    memory_refresh,
    memory_search,
    memory_status,
    reindex_vector_local,
    register_project,
    vector_index_status,
)


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    (repo / "README.md").write_text("# project\n\nA sample service.\n", encoding="utf-8")
    docs = repo / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# architecture\n\nUses pytest.\n", encoding="utf-8")
    _run(["git", "add", "."], repo)
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
                "profile": "coder",
            },
            "obsidian": {
                "enabled": True,
                "vault_root": str(tmp_path / "vault"),
                "wiki_root": "CodexWiki",
            },
        }
    )


def test_project_memory_import_commit_and_refresh(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    registration = register_project(repo, name="sample-service")
    assert registration.project_id

    preview = memory_import_preview(config, repo)
    assert preview.dry_run is True
    assert preview.sources
    assert "sample service" in preview.compact_summary.lower()

    imported = memory_import_apply(config, repo)
    assert Path(imported.manifest_path).exists()
    status = memory_status(imported.project_id)
    assert status.status == "draft"

    committed = memory_commit(config, imported.project_id)
    assert committed.status == "reviewed"
    assert committed.project_note is not None

    report = list_projects()
    assert any(project.project_id == imported.project_id for project in report.projects)

    (repo / "README.md").write_text("# project\n\nA changed service.\n", encoding="utf-8")
    refreshed = memory_refresh(config, repo, dry_run=True)
    assert refreshed.stale is True
    assert refreshed.changed_sources


def test_memory_import_preserves_registered_display_name(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    register_project(repo, name="custom-display")

    imported = memory_import_apply(config, repo)
    status = memory_status(imported.project_id)
    assert imported.display_name == "custom-display"
    assert status.display_name == "custom-display"


def test_project_memory_bootstrap_creates_project_scoped_obsidian_entrypoints(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    dry_run = project_memory_bootstrap(config, repo, dry_run=True)
    assert dry_run.dry_run is True
    assert dry_run.planned_paths
    assert not dry_run.created_paths

    result = project_memory_bootstrap(config, repo, dry_run=False)
    assert result.dry_run is False
    assert result.created_paths
    status_path = Path(config.obsidian.vault_root) / config.obsidian.wiki_root / "Projects" / result.project_id / "status.md"
    log_path = Path(config.obsidian.vault_root) / config.obsidian.wiki_root / "Tasks" / result.project_id / "log.md"
    manifest_path = Path(config.obsidian.vault_root) / config.obsidian.wiki_root / "Sources" / "_manifest.md"
    assert status_path.exists()
    assert log_path.exists()
    assert manifest_path.exists()
    assert f"project_id: {result.project_id}" in status_path.read_text(encoding="utf-8")


def test_import_outbox_note_to_memory_writes_builtin_file(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)

    outbox_root = tmp_path / "outbox"
    note_path, _ = write_outbox_note(
        outbox_root,
        note_type="project_baseline",
        task_id="project-1-baseline",
        project_id="project-1",
        workspace_id="project-1",
        title="Project Baseline: project-1",
        compact_summary="Project baseline compact summary.",
        pointer="[[CodexWiki/Projects/project-1]]",
    )

    imported = import_outbox_note_to_memory(note_path, profile="coder", target="memory")
    assert imported.exists()
    text = imported.read_text(encoding="utf-8")
    assert "Project baseline compact summary." in text


def test_memory_commit_direct_mode_writes_builtin_memory(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state")},
            "hermes": {"mode": "python_library", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder"},
            "obsidian": {"enabled": False, "vault_root": ""},
        }
    )

    imported = memory_import_apply(config, repo)
    committed = memory_commit(config, imported.project_id)
    assert committed.status == "reviewed"
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    assert memory_file.exists()
    assert imported.compact_summary in memory_file.read_text(encoding="utf-8")


def test_memory_commit_builtin_file_mode_is_first_class(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = SupervisorConfig.model_validate(
        {
            "state": {"root": str(tmp_path / "state")},
            "hermes": {"mode": "builtin_file", "outbox_dir": str(tmp_path / "outbox"), "profile": "coder"},
            "obsidian": {"enabled": False, "vault_root": ""},
        }
    )
    imported = memory_import_apply(config, repo)
    committed = memory_commit(config, imported.project_id)
    assert committed.status == "reviewed"
    memory_file = home / ".hermes" / "memories" / "MEMORY.md"
    assert memory_file.exists()
    assert imported.compact_summary in memory_file.read_text(encoding="utf-8")


def test_memory_search_finds_manifest_and_outbox(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)

    result = memory_search("sample repository", config=config)
    assert result.hits
    assert any(hit.kind == "manifest" for hit in result.hits)
    assert any(hit.kind == "outbox" for hit in result.hits)
    assert any(hit.kind == "wiki" for hit in result.hits)


def test_memory_lookup_builds_project_context_pack_and_infers_workstream(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    registration = register_project(repo, name="codex-telegram")
    project_id = registration.project_id
    wiki_root = Path(config.obsidian.vault_root) / config.obsidian.wiki_root
    stack_note = wiki_root / "Projects" / project_id / "codex-hermes-stack.md"
    stack_note.parent.mkdir(parents=True, exist_ok=True)
    stack_note.write_text(
        "---\n"
        "scope: project\n"
        f"project_id: {project_id}\n"
        "workstream_id: codex-hermes-stack\n"
        "workstream_aliases:\n"
        "  - 하네스\n"
        "  - 공식앱\n"
        "type: project\n"
        "---\n\n"
        "# Codex-Hermes Stack\n\n"
        "하네스와 공식앱 Hermes Obsidian QMD vector memory lookup 연결 테스트.\n",
        encoding="utf-8",
    )

    result = memory_lookup("하네스 공식앱", repo_root=repo, config=config, mode="keyword")

    assert result.project_id == project_id
    assert result.inferred_workstream_id == "codex-hermes-stack"
    assert result.context_pack.source_paths
    assert result.context_pack.sources[0].evidence_allowed is True
    assert result.context_pack.sources[0].hit_project_id == project_id
    assert any(candidate.workstream_id == "codex-hermes-stack" for candidate in result.workstream_candidates)


def test_memory_lookup_keeps_cross_project_hits_reference_only(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    registration = register_project(repo, name="current-project")
    wiki_root = Path(config.obsidian.vault_root) / config.obsidian.wiki_root
    other_note = wiki_root / "Projects" / "other-project" / "telegram-runtime.md"
    other_note.parent.mkdir(parents=True, exist_ok=True)
    other_note.write_text(
        "---\n"
        "scope: project\n"
        "project_id: other-project\n"
        "workstream_id: telegram-runtime\n"
        "type: project\n"
        "---\n\n"
        "# Telegram Runtime\n\n"
        "텔레그램 bridge runtime 기록.\n",
        encoding="utf-8",
    )

    result = memory_lookup("텔레그램 bridge", repo_root=repo, config=config, mode="keyword")

    assert result.project_id == registration.project_id
    assert not result.context_pack.sources
    assert result.context_pack.rejected_reference_paths
    assert any("MEMORY_LOOKUP_REFERENCE_ONLY_HITS" in warning for warning in result.warnings)


def test_memory_lookup_keeps_unverified_project_notes_reference_only(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    registration = register_project(repo, name="current-project")
    project_id = registration.project_id
    wiki_root = Path(config.obsidian.vault_root) / config.obsidian.wiki_root
    candidate_note = wiki_root / "Projects" / project_id / "candidate-note.md"
    candidate_note.parent.mkdir(parents=True, exist_ok=True)
    candidate_note.write_text(
        "---\n"
        "scope: project\n"
        f"project_id: {project_id}\n"
        "status: candidate\n"
        "review_status: unverified\n"
        "confidence: low\n"
        "evidence_class: reference_only\n"
        "---\n\n"
        "# Candidate Note\n\n"
        "unique-candidate-memory should remain reference-only until review.\n",
        encoding="utf-8",
    )

    result = memory_lookup("unique-candidate-memory", repo_root=repo, config=config, mode="keyword")

    assert result.project_id == project_id
    assert not result.context_pack.sources
    assert str(candidate_note) in result.context_pack.rejected_reference_paths
    assert any("MEMORY_SEARCH_UNVERIFIED_HITS_REFERENCE_ONLY" in warning for warning in result.warnings)
    assert any("MEMORY_LOOKUP_REFERENCE_ONLY_HITS" in warning for warning in result.warnings)


def test_memory_search_respects_custom_outbox_dir(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.hermes.outbox_dir = str(tmp_path / "custom-outbox")
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)

    result = memory_search("sample service", project_id=imported.project_id, config=config)
    assert any(hit.kind == "outbox" for hit in result.hits)
    assert all("custom-outbox" in hit.path or hit.kind != "outbox" for hit in result.hits)


def test_memory_search_prioritizes_project_wiki_note(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)

    noisy_task = Path(config.obsidian.vault_root) / config.obsidian.wiki_root / "Tasks" / "noisy-task.md"
    noisy_task.parent.mkdir(parents=True, exist_ok=True)
    noisy_task.write_text(
        "---\n"
        f"project_id: \"{imported.project_id}\"\n"
        "type: task\n"
        "---\n\n"
        "# Noisy task\n\n"
        "sample service sample service sample service sample service\n",
        encoding="utf-8",
    )

    keyword = memory_search("sample service", project_id=imported.project_id, config=config, mode="keyword")
    semantic = memory_search("sample service", project_id=imported.project_id, config=config, mode="semantic")

    assert keyword.hits[0].kind == "wiki"
    assert "\\Projects\\" in keyword.hits[0].path
    assert any("\\Projects\\" in hit.path for hit in semantic.hits[:3])


def test_memory_search_qmd_falls_back_to_semantic_lite(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "missing-qmd-executable"

    result = memory_search("sample service", backend="qmd", project_id=imported.project_id, config=config, mode="semantic")
    assert result.backend == "semantic_lite"
    assert result.hits
    assert result.warnings


def test_memory_search_qmd_backend_uses_fake_executable(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    qmd_script = tmp_path / "fake_qmd.py"
    fake_hit = tmp_path / "vault" / "CodexWiki" / "Projects" / "sample-service.md"
    fake_hit.parent.mkdir(parents=True, exist_ok=True)
    fake_hit.write_text("---\nscope: project\nproject_id: project-1\n---\n# sample\n", encoding="utf-8")
    escaped = str(fake_hit).replace("\\", "\\\\")
    qmd_script.write_text(
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "if '--version' in args:\n"
        "    print('qmd 0.0-test')\n"
        "elif args[:2] == ['collection', 'add']:\n"
        "    print('ok')\n"
        "elif args[:1] == ['embed']:\n"
        "    print('embedded')\n"
        "else:\n"
        f"    print(json.dumps({{'hits':[{{'path':'{escaped}','kind':'note','score':42,'snippet':'qmd snippet'}}]}}))\n",
        encoding="utf-8",
    )

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "py"
    config.search.qmd.collection_roots = [str(fake_hit.parent.parent)]
    monkeypatch.setattr(
        "codex_hermes_supervisor.integrations.qmd._run_qmd_command",
        lambda cfg, args: subprocess.CompletedProcess([cfg.search.qmd.executable, *args], 0, stdout='{"hits":[{"path":"qmd://codexwiki/projects/sample-service.md","kind":"note","score":42,"snippet":"qmd snippet"}]}\n', stderr=""),
    )

    result = memory_search("sample service", backend="qmd", project_id="project-1", config=config, mode="semantic")
    assert result.backend == "qmd"
    assert result.hits
    assert result.hits[0].snippet == "qmd snippet"
    assert result.hits[0].path == str(fake_hit)
    assert result.hits[0].source_read is True
    assert result.hits[0].source_sha256 is not None


def test_memory_search_qmd_marks_cross_project_hit_reference_only(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    fake_hit = tmp_path / "vault" / "CodexWiki" / "Projects" / "other-project.md"
    fake_hit.parent.mkdir(parents=True, exist_ok=True)
    fake_hit.write_text(
        "---\n"
        "project_id: other-project\n"
        "type: project\n"
        "---\n\n"
        "# Other project\n\n"
        "sample service reference\n",
        encoding="utf-8",
    )
    escaped = str(fake_hit).replace("\\", "\\\\")

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "py"
    config.search.qmd.collection_roots = [fake_hit.parent.parent]
    monkeypatch.setattr(
        "codex_hermes_supervisor.integrations.qmd._run_qmd_command",
        lambda cfg, args: subprocess.CompletedProcess(
            [cfg.search.qmd.executable, *args],
            0,
            stdout=f'{{"hits":[{{"path":"{escaped}","kind":"note","score":42,"snippet":"cross project"}}]}}\n',
            stderr="",
        ),
    )

    result = memory_search("sample service", backend="qmd", project_id="current-project", config=config, mode="keyword")

    assert result.backend == "qmd"
    assert result.hits
    assert result.hits[0].source_read is True
    assert result.hits[0].hit_project_id == "other-project"
    assert result.hits[0].current_project_id == "current-project"
    assert result.hits[0].evidence_allowed is False
    assert result.hits[0].evidence_status == "reference_candidate_cross_project"
    assert any("MEMORY_SEARCH_CROSS_PROJECT_HITS_REFERENCE_ONLY" in warning for warning in result.warnings)


def test_memory_search_qmd_semantic_uses_keyword_fallback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    fake_hit = tmp_path / "vault" / "CodexWiki" / "Projects" / "sample-service.md"
    fake_hit.parent.mkdir(parents=True, exist_ok=True)
    fake_hit.write_text("---\nscope: project\nproject_id: project-1\n---\n# sample\n", encoding="utf-8")

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "py"
    config.search.qmd.collection_roots = [str(fake_hit.parent.parent)]

    escaped = str(fake_hit).replace("\\", "\\\\")

    def _fake_qmd_search(cfg, args):
        if args and args[0] == "vsearch":
            return subprocess.CompletedProcess([cfg.search.qmd.executable, *args], 0, stdout='{"hits":[]}\n', stderr="")
        return subprocess.CompletedProcess(
            [cfg.search.qmd.executable, *args],
            0,
            stdout=f'{{"hits":[{{"path":"{escaped}","kind":"note","score":42,"snippet":"keyword fallback"}}]}}\n',
            stderr="",
        )

    monkeypatch.setattr("codex_hermes_supervisor.integrations.qmd._run_qmd_command", _fake_qmd_search)

    result = memory_search("sample service", backend="qmd", project_id="project-1", config=config, mode="semantic")
    assert result.backend == "qmd"
    assert result.hits
    assert result.hits[0].path == str(fake_hit)
    assert "QMD_SEMANTIC_EMPTY_USED_KEYWORD_FALLBACK" in result.warnings


def test_memory_search_qmd_accepts_json_with_progress_logs(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    fake_hit = tmp_path / "vault" / "CodexWiki" / "Projects" / "sample-service.md"
    fake_hit.parent.mkdir(parents=True, exist_ok=True)
    fake_hit.write_text("---\nscope: project\nproject_id: project-1\n---\n# sample\n", encoding="utf-8")

    _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "py"
    config.search.qmd.collection_roots = [str(fake_hit.parent.parent)]

    escaped = str(fake_hit).replace("\\", "\\\\")

    def _fake_qmd_search(cfg, args):
        return subprocess.CompletedProcess(
            [cfg.search.qmd.executable, *args],
            0,
            stdout=(
                f'[{{"path":"{escaped}","kind":"note","score":0.71,"snippet":"semantic hit"}}]\n'
                "Expanding query...\n"
                "Searching 4 vector queries...\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("codex_hermes_supervisor.integrations.qmd._run_qmd_command", _fake_qmd_search)

    result = memory_search("sample service", backend="qmd", project_id="project-1", config=config, mode="semantic")
    assert result.backend == "qmd"
    assert not result.warnings
    assert result.hits
    assert result.hits[0].path == str(fake_hit)
    assert result.hits[0].score == 710


def test_memory_search_qmd_resolves_normalized_uri_aliases(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    wiki_root = tmp_path / "vault" / "CodexWiki"
    wiki_root.mkdir(parents=True, exist_ok=True)
    index_path = wiki_root / "_index.md"
    index_path.write_text("---\nscope: global\n---\n# index\n", encoding="utf-8")

    _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "py"
    config.search.qmd.collection_roots = [str(wiki_root)]

    def _fake_qmd_search(cfg, args):
        return subprocess.CompletedProcess(
            [cfg.search.qmd.executable, *args],
            0,
            stdout='[{"file":"qmd://codexwiki/index.md","kind":"note","score":0.34,"snippet":"index"}]\n',
            stderr="",
        )

    monkeypatch.setattr("codex_hermes_supervisor.integrations.qmd._run_qmd_command", _fake_qmd_search)

    result = memory_search("index", backend="qmd", project_id="project-1", config=config, mode="keyword")
    assert result.backend == "qmd"
    assert not result.warnings
    assert result.hits[0].path == str(index_path)
    assert result.hits[0].source_read is True
    assert result.hits[0].evidence_allowed is True
    assert result.hits[0].evidence_status == "allowed_global"


def test_memory_search_qmd_drops_hits_outside_allowed_roots(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.backend = "qmd"
    config.search.qmd.enabled = True
    config.search.qmd.executable = "py"
    config.search.qmd.collection_roots = [str(tmp_path / "vault" / "CodexWiki")]
    config.search.qmd.fallback_to_vector_local_if_all_hits_filtered = False

    monkeypatch.setattr(
        "codex_hermes_supervisor.integrations.qmd._run_qmd_command",
        lambda cfg, args: subprocess.CompletedProcess(
            [cfg.search.qmd.executable, *args],
            0,
            stdout='{"hits":[{"path":"C:\\\\Users\\\\example\\\\.codex-hermes\\\\hermes_outbox\\\\handoff\\\\bad.md","kind":"outbox","score":7,"snippet":"bad"}]}\n',
            stderr="",
        ),
    )

    result = memory_search("sample service", backend="qmd", project_id="project-1", config=config, mode="keyword")
    assert not result.hits
    assert any("QMD_HITS_DROPPED_OUT_OF_SCOPE" in warning for warning in result.warnings)


def test_qmd_sync_collections_runs_official_commands(tmp_path: Path) -> None:
    from codex_hermes_supervisor.integrations.qmd import sync_qmd_collections

    commands: list[list[str]] = []

    def _fake_run(cfg, args):
        commands.append(args)
        return subprocess.CompletedProcess([cfg.search.qmd.executable, *args], 0, stdout="ok\n", stderr="")

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    config.search.qmd.enabled = True
    config.search.qmd.executable = "qmd"
    config.search.qmd.collection_roots = [repo / "docs", repo]

    from codex_hermes_supervisor.integrations import qmd as qmd_module

    original = qmd_module._run_qmd_command
    original_doctor = qmd_module.build_qmd_doctor_report
    try:
        qmd_module._run_qmd_command = _fake_run  # type: ignore[assignment]
        qmd_module.build_qmd_doctor_report = lambda cfg: original_doctor(cfg).model_copy(update={"executable_found": True})  # type: ignore[assignment]
        report = sync_qmd_collections(config, embed=True)
    finally:
        qmd_module._run_qmd_command = original  # type: ignore[assignment]
        qmd_module.build_qmd_doctor_report = original_doctor  # type: ignore[assignment]

    assert report.synced_collections
    assert report.refreshed_collections
    assert report.embed_completed is True
    assert any(cmd[:2] == ["collection", "add"] for cmd in commands)
    assert any(cmd[:2] == ["update", "-c"] for cmd in commands)
    assert any(cmd[:1] == ["embed"] for cmd in commands)


def test_qmd_sync_honors_command_prefix(tmp_path: Path) -> None:
    from codex_hermes_supervisor.integrations.qmd import _run_qmd_command

    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys\n"
        "print(' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    config = SupervisorConfig.model_validate(
        {
            "search": {
                "qmd": {
                    "executable": "py",
                    "command_prefix": [str(runner)],
                }
            }
        }
    )
    completed = _run_qmd_command(config, ["search", "hello", "--json"])
    assert "search hello --json" in completed.stdout


def test_qmd_path_resolver_tolerates_stripped_trailing_hyphen(tmp_path: Path) -> None:
    from codex_hermes_supervisor.integrations.qmd import _resolve_qmd_path_object

    note = tmp_path / "vault" / "Sources" / "project" / "conv-019d6f9f-5a26-73d0-9880-.md"
    note.parent.mkdir(parents=True)
    note.write_text("# source\n", encoding="utf-8")
    stripped = note.with_name("conv-019d6f9f-5a26-73d0-9880.md")

    assert _resolve_qmd_path_object(str(stripped), {}) == note.resolve()


def test_memory_lookup_auto_uses_fast_keyword_path(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)

    result = memory_lookup("sample service", repo_root=repo, config=config, mode="auto", limit=2)

    assert result.mode == "keyword"
    assert result.context_pack.sources
    assert all(source.source_read for source in result.context_pack.sources)


def test_memory_lookup_timeout_returns_degraded_context(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)

    result = memory_lookup("sample service", repo_root=repo, config=config, mode="hybrid", timeout_seconds=0)

    assert result.lookup_deadline_exceeded is True
    assert result.lookup_timeout_seconds == 0
    assert result.lookup_timing_ms["total"] >= 0
    assert not result.context_pack.sources
    assert any("MEMORY_LOOKUP_DEADLINE_EXCEEDED" in warning for warning in result.warnings)


def test_qmd_doctor_uses_command_prefix_for_version(tmp_path: Path) -> None:
    from codex_hermes_supervisor.integrations.qmd import build_qmd_doctor_report

    runner = tmp_path / "runner.py"
    runner.write_text("print('qmd 9.9-test')\n", encoding="utf-8")
    config = SupervisorConfig.model_validate(
        {
            "search": {
                "backend": "qmd",
                "qmd": {
                    "executable": "py",
                    "command_prefix": [str(runner)],
                    "collection_roots": [str(tmp_path / 'vault')],
                },
            }
        }
    )
    report = build_qmd_doctor_report(config)
    assert report.version == "qmd 9.9-test"


def test_vector_local_reindex_and_search(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    imported = memory_import_apply(config, repo)
    memory_commit(config, imported.project_id)
    status = reindex_vector_local(config, project_id=imported.project_id)
    assert status.exists is True
    assert status.documents >= 3

    result = memory_search("sample service", backend="vector_local", project_id=imported.project_id, config=config, mode="semantic")
    assert result.backend == "vector_local"
    assert result.hits
    assert any(hit.kind in {"manifest", "outbox", "wiki"} for hit in result.hits)
    assert all(hit.source_read for hit in result.hits)
    assert all(hit.source_sha256 for hit in result.hits)
    assert all(hit.evidence_allowed for hit in result.hits)


def test_vector_index_status_reports_missing_index(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    config = _config(tmp_path)
    status = vector_index_status(config, project_id="missing-project")
    assert status.exists is False
    assert status.warnings


def test_vector_search_returns_warning_when_reindex_lock_busy(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.user_home", lambda: home)
    monkeypatch.setattr("codex_hermes_supervisor.core.paths.supervisor_root", lambda: home / ".codex-hermes")
    monkeypatch.setattr("codex_hermes_supervisor.integrations.hermes.user_home", lambda: home)

    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    imported = memory_import_apply(config, repo)
    lock_path = config.cache_root / "vector_local" / f"{imported.project_id}.lock.json"
    lock = WorkspaceLock(
        lock_path,
        workspace_id=f"vector-{imported.project_id}",
        task_id=imported.project_id,
        operation="vector_reindex",
        timeout_seconds=30,
        heartbeat_seconds=60,
    )
    lock.acquire()
    try:
        result = memory_search("sample service", backend="vector_local", project_id=imported.project_id, config=config, mode="semantic")
    finally:
        lock.release()
    assert result.backend == "vector_local"
    assert "VECTOR_INDEX_REBUILD_IN_PROGRESS" in result.warnings
