"""Schemas for project memory onboarding and registry state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemoryStatus = Literal["draft", "reviewed", "stale", "superseded", "rejected"]


class ProjectSourceItem(BaseModel):
    kind: Literal["repo_file", "obsidian_note", "codex_memory"] = "repo_file"
    path: str
    sha256: str
    status: MemoryStatus = "draft"


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
    context_pack: MemoryContextPack


class VectorIndexStatus(BaseModel):
    backend: Literal["vector_local"] = "vector_local"
    project_id: str | None = None
    index_path: str
    exists: bool
    documents: int = 0
    dimensions: int = 0
    warnings: list[str] = Field(default_factory=list)
