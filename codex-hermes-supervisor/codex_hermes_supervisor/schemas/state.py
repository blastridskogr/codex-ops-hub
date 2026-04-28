"""State models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .errors import ViolationItem
from .verification import VerificationResult, VerificationStep
from .versioning import ManagedFileDeclaration, ManagedFileState

Phase = Literal["IDLE", "STARTED", "PLANNED", "CHECKED", "FINISHED", "ARCHIVED"]
RiskLevel = Literal["low", "medium", "high"]


class IdentityModel(BaseModel):
    workspace_id: str
    project_id: str
    repo_root: str
    git_remote: str | None = None
    git_common_dir: str | None = None


class GitBaseline(BaseModel):
    head: str | None = None
    dirty_files_at_start: list[str] = Field(default_factory=list)
    untracked_files_at_start: list[str] = Field(default_factory=list)
    file_hashes_at_start: dict[str, str] = Field(default_factory=dict)
    submodule_status_at_start: str | None = None


class PlanMemoryContext(BaseModel):
    preflight_required: bool = False
    query: str | None = None
    memory_decision: str | None = None
    skip_reason: str | None = None
    lookup_required: bool = False
    lookup_ran: bool = False
    memory_evidence_ready: bool = False
    source_paths: list[str] = Field(default_factory=list)
    rejected_reference_paths: list[str] = Field(default_factory=list)
    workstream_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class PlanState(BaseModel):
    allowed_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)
    managed_files: list[ManagedFileDeclaration] = Field(default_factory=list)
    tests_required: list[str] = Field(default_factory=list)
    verification_steps: list[VerificationStep] = Field(default_factory=list)
    risk_level: RiskLevel = "medium"
    plan_summary: str = ""
    delete_allowed: bool = False
    rename_allowed: bool = False
    allow_submodule_changes: bool = False
    plan_revision: int = 0
    memory_context: PlanMemoryContext = Field(default_factory=PlanMemoryContext)


class IdempotencyRecord(BaseModel):
    key: str
    tool: str
    task_id: str
    created_at: str
    payload_hash: str
    result: dict[str, object] = Field(default_factory=dict)


class IdempotencyState(BaseModel):
    idempotency_schema_version: int = 1
    records: list[IdempotencyRecord] = Field(default_factory=list)


class StrictWriteRecord(BaseModel):
    path: str
    tool: str
    expected_hash: str | None = None
    recorded_at: str


class TaskState(BaseModel):
    state_schema_version: int = 1
    task_id: str
    idempotency_key: str | None = None
    task: str
    repo_root: str
    workspace_id: str
    project_id: str
    phase: Phase = "IDLE"
    created_at: str
    updated_at: str
    baseline: GitBaseline = Field(default_factory=GitBaseline)
    hermes_recall_done: bool = False
    plan: PlanState = Field(default_factory=PlanState)
    managed_files: list[ManagedFileState] = Field(default_factory=list)
    strict_write_log: list[StrictWriteRecord] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    violations: list[ViolationItem] = Field(default_factory=list)
    wiki_notes: list[str] = Field(default_factory=list)
    handoff_written: bool = False
