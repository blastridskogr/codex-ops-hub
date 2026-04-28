"""Schemas for project memory onboarding and registry state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemoryStatus = Literal["draft", "reviewed", "stale", "superseded", "rejected"]
SourceScope = Literal["global", "project", "workspace", "user", "reference"]
SourcePrivacy = Literal["public", "private", "customer", "secret", "restricted", "unknown"]
SourceType = Literal[
    "directory",
    "repo_text",
    "manual",
    "conversation",
    "terminal_log",
    "document",
    "spreadsheet",
    "presentation",
    "image",
    "web",
    "archive",
    "other",
]
SourcePromoteKind = Literal["project", "status", "task", "decision", "bug", "workflow", "source"]
SourceLifecycleStatus = Literal[
    "raw",
    "extracted",
    "compiled",
    "reviewed",
    "promoted",
    "stale",
    "superseded",
    "archived",
    "quarantined",
    "rejected",
]
ReviewStatus = Literal["not_required", "pending", "reviewed", "rejected"]
MemoryDecision = Literal["no_memory_needed", "light_lookup", "targeted_lookup", "deep_wiki_read"]
MemorySkipReason = Literal["pure_chat", "trivial_task", "no_durable_outcome", "missing_project_memory_bootstrap"]


class ProjectSourceItem(BaseModel):
    kind: Literal["repo_file", "obsidian_note", "codex_memory"] = "repo_file"
    path: str
    sha256: str
    status: MemoryStatus = "draft"


class SourceManifestEntry(BaseModel):
    """Raw-source registry entry for Official LLM Wiki source intake.

    This describes evidence before it is compiled into Markdown memory. It must
    not be treated as current-project evidence until source read, provenance,
    scope filtering, and any required review/promote step pass at runtime.
    """

    source_id: str
    source_type: SourceType
    content_type: str | None = None
    source_uri: str
    raw_storage_uri: str | None = None
    original_name: str | None = None
    source_title: str | None = None
    source_author: str | None = None
    source_published_at: str | None = None
    source_accessed_at: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    repo_root: str | None = None
    scope: SourceScope = "project"
    source_scope_reason: str = ""
    sha256: str
    size_bytes: int
    privacy: SourcePrivacy = "unknown"
    source_owner: Literal["user", "project", "external", "customer", "unknown"] = "unknown"
    license_or_terms: str | None = None
    extractor: str | None = None
    extractor_version: str | None = None
    extraction_status: Literal["not_started", "extracted", "failed", "not_supported"] = "not_started"
    extracted_text_sha256: str | None = None
    redaction_status: Literal["not_required", "pending", "redacted", "failed"] = "not_required"
    review_status: ReviewStatus = "pending"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    retention_policy: str | None = None
    compiled_into: list[str] = Field(default_factory=list)
    status: SourceLifecycleStatus = "raw"
    quarantine_reason: str | None = None
    notes: str = ""


class SourceManifest(BaseModel):
    source_manifest_schema_version: int = 1
    entries: list[SourceManifestEntry] = Field(default_factory=list)


class SourceStatusReport(BaseModel):
    project_id: str
    workspace_id: str
    repo_root: str
    manifest_path: str
    exists: bool
    total_entries: int = 0
    pending_review: int = 0
    reviewed: int = 0
    compiled: int = 0
    warnings: list[str] = Field(default_factory=list)


class SourceIngestResult(BaseModel):
    project_id: str
    workspace_id: str
    repo_root: str
    manifest_path: str
    dry_run: bool = True
    created: bool = False
    updated: bool = False
    entry: SourceManifestEntry
    warnings: list[str] = Field(default_factory=list)


class CodexSessionIngestProjectSummary(BaseModel):
    project_id: str
    workspace_id: str
    repo_root: str
    manifest_path: str
    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    bytes: int = 0


class CodexSessionIngestReport(BaseModel):
    dry_run: bool = True
    codex_home: str
    include_archived: bool = True
    include_backups: bool = False
    scanned_files: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    total_size_bytes: int = 0
    project_summaries: list[CodexSessionIngestProjectSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceReviewResult(BaseModel):
    project_id: str
    workspace_id: str
    repo_root: str
    manifest_path: str
    dry_run: bool = True
    entry: SourceManifestEntry
    warnings: list[str] = Field(default_factory=list)


class SourceNoteFrontmatter(BaseModel):
    """Compiled Markdown note frontmatter.

    `evidence_allowed` is intentionally absent. Evidence allowance is a runtime
    decision, not permanent note metadata.
    """

    title: str
    scope: SourceScope = "project"
    project_id: str | None = None
    workspace_id: str | None = None
    repo_root: str | None = None
    workstream_id: str | None = None
    memory_kind: Literal[
        "project",
        "task",
        "decision",
        "bug",
        "workflow",
        "source",
        "status",
        "log",
        "imported_lesson",
    ]
    status: MemoryStatus = "draft"
    review_status: Literal["unverified", "reviewed", "verified", "rejected"] = "unverified"
    confidence: Literal["high", "medium", "low"] = "medium"
    evidence_class: Literal["reference_only", "candidate_evidence", "accepted_evidence", "operational_entrypoint"] = "reference_only"
    source_type: SourceType | None = None
    privacy: SourcePrivacy | None = None
    source_id: str | None = None
    raw_storage_uri: str | None = None
    size_bytes: int | None = None
    source_refs: list[str] = Field(default_factory=list)
    source_hashes: list[str] = Field(default_factory=list)
    source_unknown_reason: str | None = None
    compiled_from: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ImportedLessonFrontmatter(SourceNoteFrontmatter):
    memory_kind: Literal["imported_lesson"] = "imported_lesson"
    imported_from_project_id: str
    promotion_reason: str
    promotion_review_status: ReviewStatus = "pending"
    imported_at: str


class SourceCompileResult(BaseModel):
    project_id: str
    workspace_id: str
    repo_root: str
    manifest_path: str
    dry_run: bool = True
    compiled: bool = False
    planned_note_path: str | None = None
    frontmatter: SourceNoteFrontmatter
    warnings: list[str] = Field(default_factory=list)


class SourcePromoteResult(BaseModel):
    project_id: str
    workspace_id: str
    repo_root: str
    manifest_path: str
    dry_run: bool = True
    promoted: bool = False
    source_id: str
    memory_kind: SourcePromoteKind
    planned_note_path: str | None = None
    frontmatter: SourceNoteFrontmatter
    warnings: list[str] = Field(default_factory=list)


class CodexSessionCompileProjectSummary(BaseModel):
    project_id: str
    workspace_id: str | None = None
    repo_root: str | None = None
    scanned: int = 0
    planned: int = 0
    compiled: int = 0
    skipped: int = 0
    index_path: str | None = None


class CodexSessionCompileReport(BaseModel):
    dry_run: bool = True
    project_id: str | None = None
    scanned_sources: int = 0
    planned: int = 0
    compiled: int = 0
    skipped: int = 0
    project_summaries: list[CodexSessionCompileProjectSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProjectWikiItem(BaseModel):
    type: str
    path: str
    wikilink: str
    status: MemoryStatus = "draft"


class ProjectOutboxItem(BaseModel):
    path: str
    status: Literal["pending_import", "imported", "archived"]
    content_hash: str


class CompactSummaryRecord(BaseModel):
    status: MemoryStatus = "draft"
    text: str = ""


class StalenessRecord(BaseModel):
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)


class ProjectMemoryManifest(BaseModel):
    memory_manifest_schema_version: int = 1
    project_id: str
    display_name: str
    repo_root: str
    status: MemoryStatus = "draft"
    created_at: str
    updated_at: str
    sources: list[ProjectSourceItem] = Field(default_factory=list)
    wiki_notes: list[ProjectWikiItem] = Field(default_factory=list)
    hermes_outbox: list[ProjectOutboxItem] = Field(default_factory=list)
    compact_summary: CompactSummaryRecord = Field(default_factory=CompactSummaryRecord)
    staleness: StalenessRecord = Field(default_factory=StalenessRecord)


class ProjectRegistryEntry(BaseModel):
    project_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    repo_remotes: list[str] = Field(default_factory=list)
    known_workspaces: list[str] = Field(default_factory=list)
    project_root: str
    manifest_path: str | None = None
    project_note: str | None = None
    status: MemoryStatus = "draft"
    last_refreshed_at: str | None = None


class ProjectRegistry(BaseModel):
    project_registry_schema_version: int = 1
    projects: list[ProjectRegistryEntry] = Field(default_factory=list)


class ProjectRegistrationResult(BaseModel):
    project_id: str
    project_root: str
    project_dir: str
    registry_path: str
    created: bool


class MemoryImportPreview(BaseModel):
    project_id: str
    display_name: str
    repo_root: str
    mode: str
    sources: list[ProjectSourceItem] = Field(default_factory=list)
    compact_summary: str = ""
    project_note_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = True


class MemoryImportResult(BaseModel):
    project_id: str
    display_name: str
    manifest_path: str
    project_note_path: str | None = None
    compact_summary: str = ""
    dry_run: bool = False


class MemoryStatusReport(BaseModel):
    project_id: str
    display_name: str
    status: MemoryStatus
    manifest_path: str | None = None
    project_note: str | None = None
    compact_summary: str = ""
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)


class ProjectMemoryBootstrapResult(BaseModel):
    project_id: str
    repo_root: str
    dry_run: bool = True
    storage_backend: str = "obsidian_codexwiki"
    created_paths: list[str] = Field(default_factory=list)
    existing_paths: list[str] = Field(default_factory=list)
    planned_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MemoryRefreshResult(BaseModel):
    project_id: str
    display_name: str
    stale: bool
    stale_reasons: list[str] = Field(default_factory=list)
    changed_sources: list[str] = Field(default_factory=list)
    dry_run: bool = True


class MemorySearchHit(BaseModel):
    kind: Literal["manifest", "note", "outbox", "wiki"]
    path: str
    project_id: str | None = None
    score: int = 0
    snippet: str = ""
    source_read: bool = False
    source_sha256: str | None = None
    source_last_modified: str | None = None
    hit_scope: str | None = None
    hit_project_id: str | None = None
    current_project_id: str | None = None
    source_status: str | None = None
    source_review_status: str | None = None
    source_evidence_class: str | None = None
    source_confidence: str | None = None
    evidence_allowed: bool = False
    evidence_status: str = "unverified"


class MemorySearchResult(BaseModel):
    query: str
    project_id: str | None = None
    mode: Literal["keyword", "semantic", "hybrid"] = "keyword"
    backend: Literal["semantic_lite", "vector_local", "qmd"] = "semantic_lite"
    warnings: list[str] = Field(default_factory=list)
    hits: list[MemorySearchHit] = Field(default_factory=list)


class WorkstreamCandidate(BaseModel):
    workstream_id: str
    score: int = 0
    reason: str = ""
    source_paths: list[str] = Field(default_factory=list)


class MemoryContextPack(BaseModel):
    query: str
    project_id: str
    workspace_id: str
    repo_root: str
    workstream_id: str | None = None
    storage_backend: str = "obsidian_codexwiki"
    source_paths: list[str] = Field(default_factory=list)
    sources: list[MemorySearchHit] = Field(default_factory=list)
    rejected_reference_paths: list[str] = Field(default_factory=list)
    summary: str = ""
    next_actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MemoryLookupResult(BaseModel):
    query: str
    project_id: str
    workspace_id: str
    repo_root: str
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    backend: Literal["semantic_lite", "vector_local", "qmd"] = "semantic_lite"
    requested_workstream_id: str | None = None
    inferred_workstream_id: str | None = None
    workstream_candidates: list[WorkstreamCandidate] = Field(default_factory=list)
    search_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    lookup_timing_ms: dict[str, int] = Field(default_factory=dict)
    lookup_timeout_seconds: float | None = None
    lookup_deadline_exceeded: bool = False
    context_pack: MemoryContextPack


class MemoryPreflightResult(BaseModel):
    query: str
    project_id: str
    workspace_id: str
    repo_root: str
    memory_decision: MemoryDecision
    skip_reason: str | None = None
    lookup_required: bool = False
    lookup_ran: bool = False
    memory_evidence_ready: bool = False
    source_paths: list[str] = Field(default_factory=list)
    rejected_reference_paths: list[str] = Field(default_factory=list)
    workstream_id: str | None = None
    lookup_result: MemoryLookupResult | None = None
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class VectorIndexStatus(BaseModel):
    backend: Literal["vector_local"] = "vector_local"
    project_id: str | None = None
    index_path: str
    exists: bool
    documents: int = 0
    dimensions: int = 0
    warnings: list[str] = Field(default_factory=list)
