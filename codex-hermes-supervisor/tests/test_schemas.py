from __future__ import annotations

from codex_hermes_supervisor.schemas.responses import ResponseEnvelope
from codex_hermes_supervisor.schemas.tools import HarnessApplyPatchInput, HarnessBeginInput, HarnessWriteVersionInput
from codex_hermes_supervisor.schemas.verification import (
    LegacyTestRun,
    VerificationResult,
    normalize_verification_results,
)


def test_response_envelope_success() -> None:
    envelope = ResponseEnvelope[dict].success(tool="harness_begin", data={"phase": "STARTED"})
    assert envelope.ok is True
    assert envelope.data == {"phase": "STARTED"}
    assert envelope.errors == []


def test_harness_begin_input_requires_repo_root_and_task() -> None:
    model = HarnessBeginInput(repo_root=r"C:\repo", task="fix bug")
    assert model.repo_root == r"C:\repo"
    assert model.task == "fix bug"


def test_normalize_verification_results_prefers_canonical_results() -> None:
    canonical = [VerificationResult(kind="manual_review", status="passed", evidence="Reviewed")]
    legacy = [LegacyTestRun(command="pytest", status="passed", notes="Legacy")]
    normalized = normalize_verification_results(canonical, legacy)
    assert normalized == canonical


def test_normalize_verification_results_from_legacy_tests() -> None:
    legacy = [LegacyTestRun(command="pytest tests/test_one.py", status="passed", notes="ok")]
    normalized = normalize_verification_results(None, legacy)
    assert len(normalized) == 1
    assert normalized[0].kind == "test"
    assert normalized[0].command == "pytest tests/test_one.py"
    assert normalized[0].evidence == "ok"


def test_strict_mode_write_inputs_require_paths_and_content() -> None:
    write_version = HarnessWriteVersionInput(
        repo_root=r"C:\repo",
        task_id="task-1",
        active_path="tools/TEST.py",
        version_path="tools/TEST_V0.1.py",
        content="print('hello')\n",
    )
    patch = HarnessApplyPatchInput(
        repo_root=r"C:\repo",
        task_id="task-1",
        target_path="README.md",
        updated_text="# updated\n",
    )
    assert write_version.active_path == "tools/TEST.py"
    assert patch.target_path == "README.md"
