from __future__ import annotations

from codex_hermes_supervisor.schemas.responses import ResponseEnvelope
from codex_hermes_supervisor.schemas.project_memory import (
    ImportedLessonFrontmatter,
    SourceManifest,
    SourceManifestEntry,
    SourceNoteFrontmatter,
)
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


def test_source_manifest_entry_tracks_review_and_provenance() -> None:
    entry = SourceManifestEntry(
        source_id="src-001",
        source_type="repo_text",
        source_uri="repo://README.md",
        original_name="README.md",
        project_id="project-1",
        workspace_id="workspace-1",
        repo_root=r"C:\projects\sample",
        scope="project",
        source_scope_reason="belongs to the current project repo",
        sha256="0" * 64,
        size_bytes=123,
        privacy="public",
        source_owner="project",
        review_status="not_required",
    )
    manifest = SourceManifest(entries=[entry])

    assert manifest.source_manifest_schema_version == 1
    assert manifest.entries[0].status == "raw"
    assert manifest.entries[0].compiled_into == []


def test_source_note_frontmatter_keeps_evidence_allowed_runtime_only() -> None:
    note = SourceNoteFrontmatter(
        title="Decision: sample",
        scope="project",
        project_id="project-1",
        memory_kind="decision",
        source_refs=["repo://README.md"],
        source_hashes=["sha256:" + "0" * 64],
    )

    assert note.memory_kind == "decision"
    assert "evidence_allowed" not in SourceNoteFrontmatter.model_fields


def test_imported_lesson_frontmatter_records_source_project() -> None:
    note = ImportedLessonFrontmatter(
        title="Imported lesson: retry QMD as keyword",
        scope="project",
        project_id="project-c",
        imported_from_project_id="project-u",
        promotion_reason="same QMD timeout failure mode",
        promotion_review_status="reviewed",
        imported_at="2026-04-28T00:00:00+09:00",
        source_refs=["CodexWiki/Projects/project-u/qmd-timeout.md"],
        source_hashes=["sha256:" + "1" * 64],
    )

    assert note.memory_kind == "imported_lesson"
    assert note.imported_from_project_id == "project-u"
