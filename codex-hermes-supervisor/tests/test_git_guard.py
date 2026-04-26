from __future__ import annotations

import subprocess
from pathlib import Path

from codex_hermes_supervisor.integrations.git import capture_git_baseline
from codex_hermes_supervisor.schemas.state import TaskState
from codex_hermes_supervisor.services.git_guard import compute_finish_fingerprint, run_harness_check


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    return repo


def _commit_file(repo: Path, rel_path: str, content: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _run(["git", "add", rel_path], repo)
    _run(["git", "commit", "-m", f"add {rel_path}"], repo)


def _make_state(repo: Path) -> TaskState:
    baseline = capture_git_baseline(repo)
    return TaskState(
        task_id="task-1",
        task="fix bug",
        repo_root=str(repo),
        workspace_id="workspace-1",
        project_id="project-1",
        created_at="2026-04-21T18:55:33+09:00",
        updated_at="2026-04-21T18:55:33+09:00",
        phase="PLANNED",
        baseline=baseline,
    )


def test_harness_check_detects_outside_allowed_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/app.py", "print('a')\n")
    state = _make_state(repo)
    state.plan.allowed_files = ["src/only.py"]
    (repo / "src/app.py").write_text("print('b')\n", encoding="utf-8")
    result = run_harness_check(repo, state)
    assert result.check_passed is False
    assert any(item.type == "outside_allowed_files" for item in result.violations)


def test_harness_check_ignores_baseline_dirty_without_additional_change(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/app.py", "print('a')\n")
    (repo / "src/app.py").write_text("print('dirty')\n", encoding="utf-8")
    state = _make_state(repo)
    state.plan.allowed_files = ["src/app.py"]
    result = run_harness_check(repo, state)
    assert result.check_passed is True
    assert result.changed_files == []


def test_harness_check_detects_baseline_dirty_modified_again(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/app.py", "print('a')\n")
    (repo / "src/app.py").write_text("print('dirty')\n", encoding="utf-8")
    state = _make_state(repo)
    state.plan.allowed_files = ["src/app.py"]
    (repo / "src/app.py").write_text("print('dirty again')\n", encoding="utf-8")
    result = run_harness_check(repo, state)
    assert result.changed_files


def test_harness_check_blocks_delete_without_allow_flag(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/app.py", "print('a')\n")
    state = _make_state(repo)
    state.plan.allowed_files = ["src/app.py"]
    (repo / "src/app.py").unlink()
    result = run_harness_check(repo, state)
    assert any(item.type == "delete_not_allowed" for item in result.violations)


def test_finish_fingerprint_changes_when_repo_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/app.py", "print('a')\n")
    state = _make_state(repo)
    first = compute_finish_fingerprint(repo, state)
    (repo / "src/app.py").write_text("print('b')\n", encoding="utf-8")
    second = compute_finish_fingerprint(repo, state)
    assert first != second
