"""MCP tool input and output models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .errors import ViolationItem
from .project_memory import MemoryPreflightResult
from .state import GitBaseline, IdentityModel, PlanState, RiskLevel, TaskState
from .verification import LegacyTestRun, VerificationResult, VerificationStep
from .versioning import ManagedFileDeclaration, VersionPrepareData, VersionSyncData
from .wiki import WikiNoteData, WikiNoteInput


class HarnessBeginInput(BaseModel):
    repo_root: str
    task: str
    risk_hint: str | None = None
    force_new_task: bool = False
    idempotency_key: str | None = None


class HermesRecallSource(BaseModel):
    source_type: str
    path: str | None = None
    included_chars: int | None = None
    original_chars: int | None = None
    truncated: bool = False
    status: str = "used"
    overflow_policy: str | None = None
    scope: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    repo_root: str | None = None
    memory_kind: str | None = None


class HermesRecallFilter(BaseModel):
    current_project_id: str | None = None
    current_workspace_id: str | None = None
    allowed_count: int = 0
    filtered_cross_project_count: int = 0
    legacy_unscoped_count: int = 0
    rejected_missing_identity_count: int = 0
    rejected_invalid_scope_count: int = 0


class HermesRecallData(BaseModel):
    requested_mode: str
    actual_mode: str
    fallback: bool = False
    fallback_reason: str | None = None
    recall_available: bool
    recall: str = ""
    sources: list[HermesRecallSource] = Field(default_factory=list)
    recall_filter: HermesRecallFilter | None = None


class TaskRecordSummary(BaseModel):
    todo: str = ""
    latest_lessons: list[str] = Field(default_factory=list)


class HarnessBeginData(BaseModel):
    task_id: str
    identity: IdentityModel
    phase: str
    baseline: GitBaseline
    hermes: HermesRecallData
    task_record_summary: TaskRecordSummary
    next_required_tool: str


class HarnessPlanInput(BaseModel):
    repo_root: str
    task_id: str
    plan_summary: str
    allowed_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)
    managed_files: list[ManagedFileDeclaration] = Field(default_factory=list)
    tests_required: list[str] = Field(default_factory=list)
    verification_steps: list[VerificationStep] = Field(default_factory=list)
    risk_level: RiskLevel = "medium"
    delete_allowed: bool = False
    rename_allowed: bool = False
    allow_submodule_changes: bool = False
    memory_preflight: MemoryPreflightResult | None = None
    require_memory_preflight: bool = False
    idempotency_key: str | None = None


class HarnessPlanData(BaseModel):
    phase: str
    plan: PlanState
    next_required_tool: str


class HarnessCheckInput(BaseModel):
    repo_root: str
    task_id: str
    include_staged: bool = True
    include_untracked: bool = True
    idempotency_key: str | None = None


class HarnessCheckpointInput(BaseModel):
    repo_root: str
    task_id: str
    kind: str = Field(pattern="^(progress|decision|failed_attempt|verification|risk|blocker|note)$")
    summary: str
    evidence: list[str] = Field(default_factory=list)
    next_action: str | None = None
    idempotency_key: str | None = None


class HarnessCheckpointData(BaseModel):
    recorded: bool
    worklog_path: str
    entry: str


class ChangedFileItem(BaseModel):
    path: str
    status: str
    staged: bool = False


class HarnessCheckData(BaseModel):
    check_passed: bool
    phase: str
    changed_files: list[ChangedFileItem] = Field(default_factory=list)
    violations: list[ViolationItem] = Field(default_factory=list)
    pending_versions: list[str] = Field(default_factory=list)
    active_mirror_checks: list[dict[str, object]] = Field(default_factory=list)


class HarnessFinishInput(BaseModel):
    repo_root: str
    task_id: str
    summary: str
    tests_run: list[LegacyTestRun] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    failed_attempts: list[str] = Field(default_factory=list)
    create_wiki_note: bool = False
    require_wiki_note: bool = False
    finish_even_with_warnings: bool = False
    idempotency_key: str | None = None


class HandoffData(BaseModel):
    mode: str
    requested_mode: str
    actual_mode: str
    path: str
    fallback: bool = False
    fallback_reason: str | None = None


class HarnessFinishData(BaseModel):
    phase: str
    worklog_path: str
    handoff: HandoffData | None = None
    wiki_note: WikiNoteData | None = None
    state: TaskState | None = None


class LessonCaptureInput(BaseModel):
    repo_root: str
    task_id: str
    correction: str
    mistake_pattern: str
    prevention_rule: str
    idempotency_key: str | None = None


class LessonCaptureData(BaseModel):
    lesson_recorded: bool
    lessons_path: str
    hermes_outbox_path: str | None = None


class VersionPrepareInput(BaseModel):
    repo_root: str
    task_id: str
    active_path: str
    reason: str
    versioning_mode: str = "side_by_side"
    idempotency_key: str | None = None


class VersionSyncInput(BaseModel):
    repo_root: str
    task_id: str
    active_path: str
    version_path: str
    idempotency_key: str | None = None


class HarnessWriteVersionInput(BaseModel):
    repo_root: str
    task_id: str
    active_path: str
    version_path: str
    content: str
    encoding: str = "utf-8"
    idempotency_key: str | None = None


class HarnessWriteVersionData(BaseModel):
    active_path: str
    version_path: str
    bytes_written: int
    pending: bool = True
    sync_required: bool = True
    instruction: str


class HarnessApplyPatchInput(BaseModel):
    repo_root: str
    task_id: str
    target_path: str
    updated_text: str
    expected_current_text: str | None = None
    create_if_missing: bool = False
    encoding: str = "utf-8"
    idempotency_key: str | None = None


class HarnessApplyPatchData(BaseModel):
    path: str
    bytes_written: int
    changed: bool
    created: bool = False


__all__ = [
    "HarnessApplyPatchData",
    "HarnessApplyPatchInput",
    "HarnessBeginData",
    "HarnessBeginInput",
    "HarnessCheckpointData",
    "HarnessCheckpointInput",
    "HarnessCheckData",
    "HarnessCheckInput",
    "HarnessFinishData",
    "HarnessFinishInput",
    "HarnessPlanData",
    "HarnessPlanInput",
    "HarnessWriteVersionData",
    "HarnessWriteVersionInput",
    "HermesRecallFilter",
    "HermesRecallSource",
    "LessonCaptureData",
    "LessonCaptureInput",
    "TaskRecordSummary",
    "VersionPrepareData",
    "VersionPrepareInput",
    "VersionSyncData",
    "VersionSyncInput",
    "WikiNoteData",
    "WikiNoteInput",
]
