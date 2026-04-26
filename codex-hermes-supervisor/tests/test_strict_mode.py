from __future__ import annotations

import subprocess
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.harness import (
    harness_apply_patch_tool,
    harness_begin,
    harness_check,
    harness_plan,
    harness_write_version_tool,
    version_sync_tool,
    version_prepare_tool,
)
from codex_hermes_supervisor.schemas.tools import (
    HarnessApplyPatchInput,
    HarnessBeginInput,
    HarnessCheckInput,
    HarnessPlanInput,
    HarnessWriteVersionInput,
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


def _config(tmp_path: Path, *, strict_mode: dict[str, object] | None = None) -> SupervisorConfig:
    payload: dict[str, object] = {
        "state": {"root": str(tmp_path / "state"), "lock_timeout_seconds": 30, "heartbeat_seconds": 1},
        "hermes": {"mode": "outbox", "outbox_dir": str(tmp_path / "outbox")},
        "obsidian": {"enabled": False, "vault_root": ""},
    }
    if strict_mode is not None:
        payload["strict_mode"] = strict_mode
    return SupervisorConfig.model_validate(payload)


def test_harness_write_version_requires_strict_mode(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(tmp_path)
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="strict write"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Allow managed file writes",
            allowed_files=["tools/TEST.py"],
            managed_files=[{"active_path": "tools/TEST.py", "reason": "generated", "versioning_mode": "side_by_side"}],
            risk_level="low",
        ),
    )
    assert plan.ok is True
    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(repo_root=str(repo), task_id=task_id, active_path="tools/TEST.py", reason="create"),
    )
    assert prepared.ok is True

    write = harness_write_version_tool(
        config,
        HarnessWriteVersionInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/TEST.py",
            version_path=prepared.data.version_path,
            content="print('hello')\n",
        ),
    )
    assert write.ok is False
    assert write.errors[0].code == "STRICT_MODE_NOT_ENABLED"


def test_harness_write_version_updates_pending_snapshot_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(
        tmp_path,
        strict_mode={
            "enabled": True,
            "write_policy": "supervisor_only",
            "allow_direct_codex_edits": False,
            "allow_supervisor_managed_file_write": True,
            "allow_supervisor_apply_patch": False,
        },
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="strict write"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Allow managed file writes",
            allowed_files=["tools/TEST.py"],
            managed_files=[{"active_path": "tools/TEST.py", "reason": "generated", "versioning_mode": "side_by_side"}],
            risk_level="low",
        ),
    )
    assert plan.ok is True
    prepared = version_prepare_tool(
        config,
        VersionPrepareInput(repo_root=str(repo), task_id=task_id, active_path="tools/TEST.py", reason="create"),
    )
    assert prepared.ok is True

    write = harness_write_version_tool(
        config,
        HarnessWriteVersionInput(
            repo_root=str(repo),
            task_id=task_id,
            active_path="tools/TEST.py",
            version_path=prepared.data.version_path,
            content="print('strict')\n",
        ),
    )
    assert write.ok is True
    assert Path(prepared.data.version_path).read_text(encoding="utf-8") == "print('strict')\n"
    assert not (repo / "tools" / "TEST.py").exists()
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


def test_harness_check_flags_direct_edit_without_supervisor(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(
        tmp_path,
        strict_mode={
            "enabled": True,
            "write_policy": "supervisor_only",
            "allow_direct_codex_edits": False,
            "allow_supervisor_managed_file_write": False,
            "allow_supervisor_apply_patch": True,
        },
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="strict direct edit"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Allow README only through supervisor patch",
            allowed_files=["README.md"],
            risk_level="low",
        ),
    )
    assert plan.ok is True

    (repo / "README.md").write_text("# edited directly\n", encoding="utf-8")
    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.ok is True
    assert checked.data.check_passed is False
    violation_types = {item.type for item in checked.data.violations}
    assert "write_without_supervisor" in violation_types


def test_harness_check_flags_hash_drift_after_supervisor_write(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(
        tmp_path,
        strict_mode={
            "enabled": True,
            "write_policy": "supervisor_only",
            "allow_direct_codex_edits": False,
            "allow_supervisor_managed_file_write": False,
            "allow_supervisor_apply_patch": True,
        },
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="strict hash drift"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Allow README patch",
            allowed_files=["README.md"],
            risk_level="low",
        ),
    )
    assert plan.ok is True
    patch = harness_apply_patch_tool(
        config,
        HarnessApplyPatchInput(
            repo_root=str(repo),
            task_id=task_id,
            target_path="README.md",
            updated_text="# patched by supervisor\n",
            expected_current_text="# test\n",
        ),
    )
    assert patch.ok is True

    (repo / "README.md").write_text("# edited after supervisor\n", encoding="utf-8")
    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.ok is True
    assert checked.data.check_passed is False
    violation_types = {item.type for item in checked.data.violations}
    assert "direct_edit_detected" in violation_types


def test_harness_apply_patch_writes_allowed_file_under_strict_mode(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(
        tmp_path,
        strict_mode={
            "enabled": True,
            "write_policy": "supervisor_only",
            "allow_direct_codex_edits": False,
            "allow_supervisor_managed_file_write": False,
            "allow_supervisor_apply_patch": True,
        },
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="patch readme"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Allow README patch",
            allowed_files=["README.md"],
            risk_level="low",
        ),
    )
    assert plan.ok is True

    patch = harness_apply_patch_tool(
        config,
        HarnessApplyPatchInput(
            repo_root=str(repo),
            task_id=task_id,
            target_path="README.md",
            updated_text="# updated\n",
            expected_current_text="# test\n",
        ),
    )
    assert patch.ok is True
    assert (repo / "README.md").read_text(encoding="utf-8") == "# updated\n"

    checked = harness_check(config, HarnessCheckInput(repo_root=str(repo), task_id=task_id))
    assert checked.ok is True
    assert checked.data.check_passed is True


def test_harness_apply_patch_rejects_managed_file_targets(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    config = _config(
        tmp_path,
        strict_mode={
            "enabled": True,
            "write_policy": "supervisor_only",
            "allow_direct_codex_edits": False,
            "allow_supervisor_managed_file_write": True,
            "allow_supervisor_apply_patch": True,
        },
    )
    begin = harness_begin(config, HarnessBeginInput(repo_root=str(repo), task="patch managed"))
    task_id = begin.data.task_id
    plan = harness_plan(
        config,
        HarnessPlanInput(
            repo_root=str(repo),
            task_id=task_id,
            plan_summary="Managed file only",
            allowed_files=["tools/TEST.py"],
            managed_files=[{"active_path": "tools/TEST.py", "reason": "generated", "versioning_mode": "side_by_side"}],
            risk_level="low",
        ),
    )
    assert plan.ok is True

    patch = harness_apply_patch_tool(
        config,
        HarnessApplyPatchInput(
            repo_root=str(repo),
            task_id=task_id,
            target_path="tools/TEST.py",
            updated_text="print('nope')\n",
            create_if_missing=True,
        ),
    )
    assert patch.ok is False
    assert patch.errors[0].code == "MANAGED_FILE_WRITE_REQUIRES_VERSION_FLOW"
