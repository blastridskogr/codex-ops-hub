"""Official LLM Wiki source intake and compile dry-run services."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

import yaml

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import build_identity, normalize_windows_path
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.core.paths import supervisor_projects_root
from codex_hermes_supervisor.schemas.project_memory import (
    ReviewStatus,
    SourceCompileResult,
    SourceIngestResult,
    SourceManifest,
    SourceManifestEntry,
    SourceNoteFrontmatter,
    SourcePrivacy,
    SourceReviewResult,
    SourceScope,
    SourceStatusReport,
    SourceType,
)

_REVIEW_REQUIRED_PRIVACY = {"private", "customer", "secret", "restricted"}
_LIGHTWEIGHT_SOURCE_TYPES = {"repo_text", "manual", "conversation", "terminal_log"}
_MAX_LIGHTWEIGHT_SOURCE_SIZE = 2 * 1024 * 1024


def _project_dir(project_id: str) -> Path:
    path = supervisor_projects_root() / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _source_manifest_path(project_id: str) -> Path:
    return _project_dir(project_id) / "source_manifest.yaml"


def _load_source_manifest(project_id: str) -> SourceManifest:
    path = _source_manifest_path(project_id)
    if not path.exists():
        return SourceManifest()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SourceManifest.model_validate(data)


def _save_source_manifest(project_id: str, manifest: SourceManifest) -> Path:
    path = _source_manifest_path(project_id)
    atomic_write_text(path, yaml.safe_dump(manifest.model_dump(mode="python"), sort_keys=False, allow_unicode=False))
    return path


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _source_id_from_hash(sha256: str) -> str:
    return f"src-{sha256.removeprefix('sha256:')[:12]}"


def _resolve_repo_source(repo_root: Path, source_path: Path) -> Path:
    repo_root = repo_root.resolve()
    resolved = source_path if source_path.is_absolute() else repo_root / source_path
    resolved = resolved.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Source path does not exist: {resolved}")
    if resolved.is_dir():
        raise IsADirectoryError(f"Directory ingest is not implemented yet: {resolved}")
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Source path must be inside repo root for this phase: {resolved}") from exc
    return resolved


def _find_entry(manifest: SourceManifest, source_id: str) -> tuple[int, SourceManifestEntry] | None:
    for index, entry in enumerate(manifest.entries):
        if entry.source_id == source_id:
            return index, entry
    return None


def source_status(repo_root: Path) -> SourceStatusReport:
    identity = build_identity(repo_root)
    manifest_path = _source_manifest_path(identity.project_id)
    manifest = _load_source_manifest(identity.project_id)
    pending_review = sum(1 for entry in manifest.entries if entry.review_status == "pending")
    reviewed = sum(1 for entry in manifest.entries if entry.review_status == "reviewed")
    compiled = sum(1 for entry in manifest.entries if entry.status in {"compiled", "promoted"} or entry.compiled_into)
    return SourceStatusReport(
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        manifest_path=normalize_windows_path(manifest_path),
        exists=manifest_path.exists(),
        total_entries=len(manifest.entries),
        pending_review=pending_review,
        reviewed=reviewed,
        compiled=compiled,
    )


def source_ingest(
    repo_root: Path,
    source_path: Path,
    *,
    source_type: SourceType = "repo_text",
    privacy: SourcePrivacy = "public",
    scope: SourceScope = "project",
    dry_run: bool = True,
    notes: str = "",
) -> SourceIngestResult:
    identity = build_identity(repo_root)
    repo_root = Path(identity.repo_root)
    resolved = _resolve_repo_source(repo_root, source_path)
    sha256 = _sha256_file(resolved)
    source_id = _source_id_from_hash(sha256)
    size_bytes = resolved.stat().st_size
    content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    warnings: list[str] = []
    if source_type not in _LIGHTWEIGHT_SOURCE_TYPES:
        warnings.append("SOURCE_TYPE_HEAVY_OR_UNSUPPORTED_FOR_PHASE_3A_MANIFEST_ONLY")
    if size_bytes > _MAX_LIGHTWEIGHT_SOURCE_SIZE:
        warnings.append("SOURCE_FILE_EXCEEDS_LIGHTWEIGHT_PHASE_SIZE_LIMIT")
    review_status: ReviewStatus = "pending" if privacy in _REVIEW_REQUIRED_PRIVACY else "not_required"
    redaction_status = "pending" if privacy in _REVIEW_REQUIRED_PRIVACY else "not_required"
    source_owner = "customer" if privacy == "customer" else "project"
    entry = SourceManifestEntry(
        source_id=source_id,
        source_type=source_type,
        content_type=content_type,
        source_uri=normalize_windows_path(resolved),
        original_name=resolved.name,
        source_title=resolved.stem,
        source_accessed_at=now_local_iso(),
        project_id=identity.project_id if scope == "project" else None,
        workspace_id=identity.workspace_id if scope in {"project", "workspace"} else None,
        repo_root=identity.repo_root,
        scope=scope,
        source_scope_reason="initial source-ingest registration",
        sha256=sha256,
        size_bytes=size_bytes,
        privacy=privacy,
        source_owner=source_owner,
        extraction_status="not_started",
        redaction_status=redaction_status,
        review_status=review_status,
        status="raw",
        notes=notes,
    )
    manifest = _load_source_manifest(identity.project_id)
    existing = _find_entry(manifest, source_id)
    created = existing is None
    updated = existing is not None
    if not dry_run:
        if existing is None:
            manifest.entries.append(entry)
        else:
            manifest.entries[existing[0]] = entry
        _save_source_manifest(identity.project_id, manifest)
    return SourceIngestResult(
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        manifest_path=normalize_windows_path(_source_manifest_path(identity.project_id)),
        dry_run=dry_run,
        created=created and not dry_run,
        updated=updated and not dry_run,
        entry=entry,
        warnings=warnings,
    )


def source_review(
    repo_root: Path,
    source_id: str,
    *,
    review_status: ReviewStatus = "reviewed",
    reviewer: str | None = None,
    notes: str | None = None,
    dry_run: bool = True,
) -> SourceReviewResult:
    identity = build_identity(repo_root)
    manifest = _load_source_manifest(identity.project_id)
    found = _find_entry(manifest, source_id)
    if found is None:
        raise KeyError(f"Source id not found in source manifest: {source_id}")
    index, entry = found
    updated = entry.model_copy(
        update={
            "review_status": review_status,
            "reviewed_by": reviewer,
            "reviewed_at": now_local_iso() if review_status in {"reviewed", "rejected"} else None,
            "notes": notes if notes is not None else entry.notes,
            "status": "reviewed" if review_status == "reviewed" else ("rejected" if review_status == "rejected" else entry.status),
        }
    )
    if not dry_run:
        manifest.entries[index] = updated
        _save_source_manifest(identity.project_id, manifest)
    return SourceReviewResult(
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        manifest_path=normalize_windows_path(_source_manifest_path(identity.project_id)),
        dry_run=dry_run,
        entry=updated,
    )


def source_compile(
    config: SupervisorConfig,
    repo_root: Path,
    source_id: str,
    *,
    dry_run: bool = True,
) -> SourceCompileResult:
    identity = build_identity(repo_root)
    manifest = _load_source_manifest(identity.project_id)
    found = _find_entry(manifest, source_id)
    if found is None:
        raise KeyError(f"Source id not found in source manifest: {source_id}")
    _, entry = found
    warnings: list[str] = []
    review_required = entry.privacy in _REVIEW_REQUIRED_PRIVACY
    if review_required and entry.review_status != "reviewed":
        warnings.append("SOURCE_REVIEW_REQUIRED_BEFORE_COMPILE")
    if entry.status in {"quarantined", "rejected"}:
        warnings.append("SOURCE_STATUS_BLOCKS_COMPILE")
    if entry.source_type not in _LIGHTWEIGHT_SOURCE_TYPES:
        warnings.append("SOURCE_TYPE_COMPILE_NOT_IMPLEMENTED_FOR_HEAVY_PHASE")
    if not dry_run:
        warnings.append("SOURCE_COMPILE_APPLY_NOT_IMPLEMENTED")

    planned_note_path: str | None = None
    if config.obsidian_root is not None:
        planned_note_path = normalize_windows_path(
            config.obsidian_root / config.obsidian.wiki_root / "Sources" / identity.project_id / f"{entry.source_id}.md"
        )
    frontmatter = SourceNoteFrontmatter(
        title=f"Source: {entry.source_title or entry.original_name or entry.source_id}",
        scope="project",
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        memory_kind="source",
        status="draft",
        confidence="medium",
        source_refs=[entry.source_uri],
        source_hashes=[entry.sha256],
        compiled_from=[entry.source_id],
        tags=["official-llm-wiki", "source-intake"],
    )
    return SourceCompileResult(
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        manifest_path=normalize_windows_path(_source_manifest_path(identity.project_id)),
        dry_run=dry_run,
        compiled=False,
        planned_note_path=planned_note_path,
        frontmatter=frontmatter,
        warnings=warnings,
    )
