"""Official LLM Wiki source intake and compile dry-run services."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import Path

import yaml

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import build_identity, normalize_windows_path
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.core.paths import supervisor_projects_root, user_home
from codex_hermes_supervisor.schemas.project_memory import (
    CodexSessionIngestProjectSummary,
    CodexSessionIngestReport,
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
_SESSION_ID_RE = re.compile(r"(019[0-9a-f]{5,}-[0-9a-f-]{20,})", re.IGNORECASE)


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


def _source_id_from_session_id(session_id: str, fallback_sha256: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", session_id.lower()).strip("-")
    if slug:
        return f"conv-{slug[:24]}"
    return f"conv-{fallback_sha256.removeprefix('sha256:')[:12]}"


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


def _load_session_index(codex_home: Path) -> dict[str, dict[str, str]]:
    index_path = codex_home / "session_index.jsonl"
    if not index_path.exists():
        return {}
    index: dict[str, dict[str, str]] = {}
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = str(payload.get("id") or "").strip()
        if not session_id:
            continue
        index[session_id] = {
            "thread_name": str(payload.get("thread_name") or ""),
            "updated_at": str(payload.get("updated_at") or ""),
        }
    return index


def _iter_codex_session_files(codex_home: Path, *, include_archived: bool, include_backups: bool) -> list[Path]:
    roots = [codex_home / "sessions"]
    if include_archived:
        roots.append(codex_home / "archived_sessions")
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        pattern = "rollout-*.jsonl*" if include_backups else "rollout-*.jsonl"
        for path in sorted(root.rglob(pattern)):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(resolved)
    return paths


def _session_id_from_path(path: Path) -> str | None:
    match = _SESSION_ID_RE.search(path.name)
    return match.group(1) if match else None


def _session_meta(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index > 64:
                break
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
                return dict(item["payload"])
    return {}


def _identity_for_session(meta: dict[str, object], codex_home: Path) -> tuple[object, str]:
    cwd = str(meta.get("cwd") or "").strip()
    if cwd:
        cwd_path = Path(cwd)
        if cwd_path.exists():
            return build_identity(cwd_path), f"codex session cwd metadata: {normalize_windows_path(cwd_path)}"
    return build_identity(codex_home), "fallback: session cwd missing or unavailable"


def codex_session_ingest(
    *,
    codex_home: Path | None = None,
    include_archived: bool = True,
    include_backups: bool = False,
    dry_run: bool = True,
    limit: int | None = None,
) -> CodexSessionIngestReport:
    """Register Codex thread JSONL files as private conversation raw sources.

    This inventories conversation transcripts as Official LLM Wiki source
    material. It never copies raw conversation content into Hermes or Obsidian
    wiki notes.
    """

    codex_home = (codex_home or (user_home() / ".codex")).resolve()
    session_index = _load_session_index(codex_home)
    files = _iter_codex_session_files(codex_home, include_archived=include_archived, include_backups=include_backups)
    if limit is not None:
        files = files[:limit]
    manifests: dict[str, SourceManifest] = {}
    identities: dict[str, object] = {}
    summaries: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    skipped = 0
    created_total = 0
    updated_total = 0
    unchanged_total = 0
    total_size = 0

    for path in files:
        try:
            stat = path.stat()
            sha256 = _sha256_file(path)
            meta = _session_meta(path)
        except OSError as exc:
            skipped += 1
            warnings.append(f"CODEX_SESSION_UNREADABLE: {normalize_windows_path(path)}: {exc}")
            continue
        identity, scope_reason = _identity_for_session(meta, codex_home)
        project_id = identity.project_id
        identities[project_id] = identity
        manifest = manifests.get(project_id)
        if manifest is None:
            manifest = _load_source_manifest(project_id)
            manifests[project_id] = manifest
        session_id = str(meta.get("id") or _session_id_from_path(path) or "")
        index_entry = session_index.get(session_id, {})
        source_id = _source_id_from_session_id(session_id, sha256)
        title = str(index_entry.get("thread_name") or meta.get("thread_name") or path.stem)
        timestamp = str(meta.get("timestamp") or index_entry.get("updated_at") or "")
        notes = "Codex session transcript raw source; unverified/reference-only until compiled."
        role = meta.get("agent_role")
        nickname = meta.get("agent_nickname")
        model = meta.get("model")
        if role or nickname or model:
            notes += f" role={role or ''}; nickname={nickname or ''}; model={model or ''}."
        entry = SourceManifestEntry(
            source_id=source_id,
            source_type="conversation",
            content_type="application/x-ndjson",
            source_uri=normalize_windows_path(path),
            raw_storage_uri=normalize_windows_path(path),
            original_name=path.name,
            source_title=title,
            source_published_at=timestamp or None,
            source_accessed_at=now_local_iso(),
            project_id=project_id,
            workspace_id=identity.workspace_id,
            repo_root=identity.repo_root,
            scope="project",
            source_scope_reason=scope_reason,
            sha256=sha256,
            size_bytes=stat.st_size,
            privacy="private",
            source_owner="user",
            extractor="codex-session-ingest",
            extractor_version="1",
            extraction_status="not_started",
            redaction_status="pending",
            review_status="pending",
            retention_policy="preserve raw transcript in Codex session store; compile only reviewed summaries",
            status="raw",
            notes=notes,
        )
        found = _find_entry(manifest, source_id)
        created = found is None
        updated = False
        unchanged = False
        if found is None:
            if not dry_run:
                manifest.entries.append(entry)
        else:
            existing_index, existing_entry = found
            if existing_entry.sha256 == entry.sha256 and existing_entry.source_uri == entry.source_uri:
                unchanged = True
            else:
                updated = True
                if not dry_run:
                    manifest.entries[existing_index] = entry
        created_total += 1 if created else 0
        updated_total += 1 if updated else 0
        unchanged_total += 1 if unchanged else 0
        total_size += stat.st_size
        summary = summaries.setdefault(
            project_id,
            {
                "project_id": project_id,
                "workspace_id": identity.workspace_id,
                "repo_root": identity.repo_root,
                "manifest_path": normalize_windows_path(_source_manifest_path(project_id)),
                "scanned": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "bytes": 0,
            },
        )
        summary["scanned"] = int(summary["scanned"]) + 1
        summary["created"] = int(summary["created"]) + (1 if created else 0)
        summary["updated"] = int(summary["updated"]) + (1 if updated else 0)
        summary["unchanged"] = int(summary["unchanged"]) + (1 if unchanged else 0)
        summary["bytes"] = int(summary["bytes"]) + stat.st_size

    if not dry_run:
        for project_id, manifest in manifests.items():
            _save_source_manifest(project_id, manifest)

    project_summaries = [
        CodexSessionIngestProjectSummary.model_validate(summary)
        for summary in sorted(summaries.values(), key=lambda item: (str(item["repo_root"]).lower(), str(item["project_id"])))
    ]
    return CodexSessionIngestReport(
        dry_run=dry_run,
        codex_home=normalize_windows_path(codex_home),
        include_archived=include_archived,
        include_backups=include_backups,
        scanned_files=len(files) - skipped,
        created=created_total,
        updated=updated_total,
        unchanged=unchanged_total,
        skipped=skipped,
        total_size_bytes=total_size,
        project_summaries=project_summaries,
        warnings=warnings,
    )


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
