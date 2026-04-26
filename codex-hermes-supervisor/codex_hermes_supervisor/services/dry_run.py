"""Local dry-run smoke flow for the harness."""

from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.harness import (
    harness_begin,
    harness_check,
    harness_finish_tool,
    harness_plan,
    version_prepare_tool,
    version_sync_tool,
)
from codex_hermes_supervisor.schemas.tools import HarnessBeginInput, HarnessCheckInput, HarnessFinishInput, HarnessPlanInput, VersionPrepareInput, VersionSyncInput


class DryRunReport(BaseModel):
    repo_root: str
    task_id: str
    version_path: str
    active_path: str
    worklog_path: str
    handoff_path: str
    check_passed: bool
    finish_phase: str


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def run_dry_run(config: SupervisorConfig) -> DryRunReport:
    """Create a temporary git repo and execute the harness flow end-to-end."""

    with TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir) / "harness-test"
        repo.mkdir()
        _run(["git", "init"], repo)
        _run(["git", "config", "user.email", "dry-run@example.com"], repo)
        _run(["git", "config", "user.name", "Dry Run"], repo)
        (repo / "README.md").write_text("# harness-test\n", encoding="utf-8")
        _run(["git", "add", "README.md"], repo)
        _run(["git", "commit", "-m", "init"], repo)

        begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="create TEST.py"))
        if not begin.ok or begin.data is None:
            raise RuntimeError(f"dry-run harness_begin failed: {begin.model_dump()}")
        task_id = begin.data.task_id

        plan = harness_plan(
            config,
            HarnessPlanInput(
                repo_root=str(repo),
                task_id=task_id,
                plan_summary="Create a standalone managed test script.",
                allowed_files=["tools/TEST.py"],
                managed_files=[{"active_path": "tools/TEST.py", "reason": "generated dry-run script", "versioning_mode": "side_by_side"}],
                risk_level="low",
                verification_steps=[{"kind": "manual_review", "description": "Review generated script diff", "required": True, "reason": "Smoke validation"}],
            ),
        )
        if not plan.ok:
            raise RuntimeError(f"dry-run harness_plan failed: {plan.model_dump()}")

        prepared = version_prepare_tool(
            config,
            VersionPrepareInput(
                repo_root=str(repo),
                task_id=task_id,
                active_path="tools/TEST.py",
                reason="create dry-run managed file",
            ),
        )
        if not prepared.ok or prepared.data is None:
            raise RuntimeError(f"dry-run version_prepare failed: {prepared.model_dump()}")

        version_path = Path(prepared.data.version_path)
        version_path.write_text("print('dry run')\n", encoding="utf-8")

        synced = version_sync_tool(
            config,
            VersionSyncInput(
                repo_root=str(repo),
                task_id=task_id,
                active_path="tools/TEST.py",
                version_path=prepared.data.version_path,
            ),
        )
        if not synced.ok:
            raise RuntimeError(f"dry-run version_sync failed: {synced.model_dump()}")

        checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
        if not checked.ok or checked.data is None:
            raise RuntimeError(f"dry-run harness_check failed: {checked.model_dump()}")

        finished = harness_finish_tool(
            config,
            HarnessFinishInput(
                repo_root=str(repo),
                task_id=task_id,
                summary="Created TEST.py via dry-run flow.",
                verification_results=[
                    {
                        "kind": "manual_review",
                        "status": "passed",
                        "evidence": "Smoke dry-run reviewed generated script diff.",
                        "satisfies_step": 0,
                    }
                ],
            ),
        )
        if not finished.ok or finished.data is None or finished.data.phase != "FINISHED":
            raise RuntimeError(f"dry-run harness_finish failed: {finished.model_dump()}")

        return DryRunReport(
            repo_root=str(repo),
            task_id=task_id,
            version_path=prepared.data.version_path,
            active_path=synced.data.active_path if synced.data else "tools/TEST.py",
            worklog_path=finished.data.worklog_path,
            handoff_path=finished.data.handoff.path if finished.data.handoff else "",
            check_passed=checked.data.check_passed,
            finish_phase=finished.data.phase,
        )
