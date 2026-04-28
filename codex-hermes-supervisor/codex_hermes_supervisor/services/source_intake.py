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
    CodexSessionCompileProjectSummary,
    CodexSessionCompileReport,
    CodexSessionIngestProjectSummary,
    CodexSessionIngestReport,
    ReviewStatus,
    SourceCompileResult,
    SourceIngestResult,
    SourceManifest,
    SourceManifestEntry,
    SourceNoteFrontmatter,
    SourcePrivacy,
    SourcePromoteKind,
    SourcePromoteResult,
    SourceReviewResult,
    SourceScope,
    SourceStatusReport,
    SourceType,
)

_REVIEW_REQUIRED_PRIVACY = {"private", "customer", "secret", "restricted"}
_LIGHTWEIGHT_SOURCE_TYPES = {"directory", "repo_text", "manual", "conversation", "terminal_log"}
_MAX_LIGHTWEIGHT_SOURCE_SIZE = 2 * 1024 * 1024
_SESSION_ID_RE = re.compile(r"(019[0-9a-f]{5,}-[0-9a-f-]{20,})", re.IGNORECASE)
_PROMOTION_DIRS: dict[SourcePromoteKind, str] = {
    "project": "Projects",
    "status": "Projects",
    "task": "Tasks",
    "decision": "Decisions",
    "bug": "Bugs",
    "workflow": "Workflows",
    "source": "Sources",
}


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


def _sha256_directory_registration(path: Path) -> str:
    names: list[str] = []
    try:
        names = sorted(child.name for child in path.iterdir())[:500]
    except OSError:
        names = []
    stat = path.stat()
    payload = {
        "kind": "directory-registration",
        "path": normalize_windows_path(path),
        "mtime_ns": stat.st_mtime_ns,
        "child_names_sample": names,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8", errors="replace")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _source_id_from_hash(sha256: str) -> str:
    return f"src-{sha256.removeprefix('sha256:')[:12]}"


def _source_id_from_directory_path(path: Path) -> str:
    normalized = normalize_windows_path(path)
    return f"dir-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def _source_id_from_session_id(session_id: str, fallback_sha256: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", session_id.lower()).strip("-")
    if slug:
        return f"conv-{slug[:24]}"
    return f"conv-{fallback_sha256.removeprefix('sha256:')[:12]}"


def _safe_slug(value: str, *, fallback: str, max_chars: int = 64) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        slug = fallback
    return slug[:max_chars].strip("-") or fallback


def _resolve_repo_source(repo_root: Path, source_path: Path) -> Path:
    repo_root = repo_root.resolve()
    resolved = source_path if source_path.is_absolute() else repo_root / source_path
    resolved = resolved.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Source path does not exist: {resolved}")
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


def _obsidian_wiki_root(config: SupervisorConfig) -> Path | None:
    if config.obsidian_root is None:
        return None
    return config.obsidian_root / config.obsidian.wiki_root


def _source_note_path(config: SupervisorConfig, project_id: str, source_id: str) -> Path | None:
    wiki_root = _obsidian_wiki_root(config)
    if wiki_root is None:
        return None
    return wiki_root / "Sources" / project_id / f"{source_id}.md"


def _promoted_note_path(config: SupervisorConfig, project_id: str, memory_kind: SourcePromoteKind, title: str, source_id: str) -> Path | None:
    wiki_root = _obsidian_wiki_root(config)
    if wiki_root is None:
        return None
    directory = _PROMOTION_DIRS[memory_kind]
    slug = _safe_slug(title, fallback=source_id)
    source_stem = source_id.rstrip("-") or source_id
    if memory_kind == "source":
        return wiki_root / directory / project_id / "promoted" / f"{source_stem}-{slug}.md"
    return wiki_root / directory / project_id / f"{source_stem}-{slug}.md"


def _source_note_frontmatter(entry: SourceManifestEntry, *, project_id: str, workspace_id: str | None, repo_root: str | None) -> SourceNoteFrontmatter:
    return SourceNoteFrontmatter(
        title=f"Source: {entry.source_title or entry.original_name or entry.source_id}",
        scope=entry.scope,
        project_id=project_id if entry.scope == "project" else entry.project_id,
        workspace_id=workspace_id,
        repo_root=repo_root,
        memory_kind="source",
        status="draft",
        review_status="unverified" if entry.review_status in {"pending", "not_required"} else ("rejected" if entry.review_status == "rejected" else "reviewed"),
        confidence="low" if entry.review_status == "pending" else "medium",
        evidence_class="reference_only",
        source_type=entry.source_type,
        privacy=entry.privacy,
        source_id=entry.source_id,
        raw_storage_uri=entry.raw_storage_uri or entry.source_uri,
        size_bytes=entry.size_bytes,
        source_refs=[entry.source_uri],
        source_hashes=[entry.sha256],
        compiled_from=[entry.source_id],
        tags=["official-llm-wiki", "source-intake", f"source-type-{entry.source_type}"],
    )


def _promoted_note_frontmatter(
    entry: SourceManifestEntry,
    *,
    project_id: str,
    workspace_id: str | None,
    repo_root: str | None,
    memory_kind: SourcePromoteKind,
    title: str,
    confidence: str,
) -> SourceNoteFrontmatter:
    return SourceNoteFrontmatter(
        title=title,
        scope="project",
        project_id=project_id,
        workspace_id=workspace_id,
        repo_root=repo_root,
        memory_kind=memory_kind,
        status="reviewed",
        review_status="reviewed",
        confidence=confidence,  # type: ignore[arg-type]
        evidence_class="candidate_evidence",
        source_type=entry.source_type,
        privacy=entry.privacy,
        source_id=entry.source_id,
        raw_storage_uri=entry.raw_storage_uri or entry.source_uri,
        size_bytes=entry.size_bytes,
        source_refs=[entry.source_uri],
        source_hashes=[entry.sha256],
        compiled_from=[entry.source_id],
        tags=["official-llm-wiki", "source-promote", f"memory-kind-{memory_kind}", f"source-type-{entry.source_type}"],
    )


def _yaml_frontmatter(frontmatter: SourceNoteFrontmatter) -> str:
    payload = frontmatter.model_dump(mode="python", exclude_none=True)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()


def _source_note_body(entry: SourceManifestEntry, frontmatter: SourceNoteFrontmatter) -> str:
    title = frontmatter.title
    return (
        f"---\n{_yaml_frontmatter(frontmatter)}\n---\n\n"
        f"# {title}\n\n"
        "This is an Official LLM Wiki compiled source note. It records source\n"
        "provenance only and does not copy raw source content.\n\n"
        "## Source State\n\n"
        f"- source_id: `{entry.source_id}`\n"
        f"- source_type: `{entry.source_type}`\n"
        f"- privacy: `{entry.privacy}`\n"
        f"- review_status: `{entry.review_status}`\n"
        f"- lifecycle_status: `{entry.status}`\n"
        f"- evidence_class: `reference_only`\n"
        f"- sha256: `{entry.sha256}`\n"
        f"- size_bytes: `{entry.size_bytes}`\n\n"
        "## Source Reference\n\n"
        f"- source_uri: `{entry.source_uri}`\n"
        f"- raw_storage_uri: `{entry.raw_storage_uri or entry.source_uri}`\n"
        f"- repo_root: `{entry.repo_root or ''}`\n"
        f"- source_scope_reason: {entry.source_scope_reason or 'not recorded'}\n\n"
        "## Use Rule\n\n"
        "Treat this note as `reference_only` until the source is reviewed and a\n"
        "Task, Decision, Bug, Workflow, Status, or reviewed Source note is compiled\n"
        "from it. Do not cite the raw transcript or raw source as current-task\n"
        "evidence.\n"
    )


def _promoted_note_body(
    entry: SourceManifestEntry,
    frontmatter: SourceNoteFrontmatter,
    *,
    summary: str,
    promotion_reason: str,
    reviewer: str | None,
) -> str:
    return (
        f"---\n{_yaml_frontmatter(frontmatter)}\n---\n\n"
        f"# {frontmatter.title}\n\n"
        "This note is promoted Official LLM Wiki knowledge compiled from a\n"
        "reviewed source. It contains an operator-provided summary and\n"
        "provenance only; raw source content is not copied here.\n\n"
        "## Summary\n\n"
        f"{summary.strip()}\n\n"
        "## Promotion\n\n"
        f"- memory_kind: `{frontmatter.memory_kind}`\n"
        f"- source_id: `{entry.source_id}`\n"
        f"- source_type: `{entry.source_type}`\n"
        f"- source_review_status: `{entry.review_status}`\n"
        f"- promotion_reason: {promotion_reason.strip()}\n"
        f"- reviewer: `{reviewer or ''}`\n"
        f"- promoted_at: `{now_local_iso()}`\n\n"
        "## Provenance\n\n"
        f"- source_uri: `{entry.source_uri}`\n"
        f"- raw_storage_uri: `{entry.raw_storage_uri or entry.source_uri}`\n"
        f"- sha256: `{entry.sha256}`\n"
        f"- size_bytes: `{entry.size_bytes}`\n\n"
        "## Evidence Rule\n\n"
        "This note is `candidate_evidence`. Runtime evidence filtering still\n"
        "checks project scope, source readability, status, review metadata, and\n"
        "hash/provenance before the note can be used in a task plan.\n"
    )


def _write_source_note(config: SupervisorConfig, entry: SourceManifestEntry, *, project_id: str, workspace_id: str | None, repo_root: str | None) -> Path | None:
    note_path = _source_note_path(config, project_id, entry.source_id)
    if note_path is None:
        return None
    note_path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = _source_note_frontmatter(entry, project_id=project_id, workspace_id=workspace_id, repo_root=repo_root)
    atomic_write_text(note_path, _source_note_body(entry, frontmatter))
    return note_path


def _write_promoted_note(
    config: SupervisorConfig,
    entry: SourceManifestEntry,
    *,
    project_id: str,
    workspace_id: str | None,
    repo_root: str | None,
    memory_kind: SourcePromoteKind,
    title: str,
    summary: str,
    promotion_reason: str,
    reviewer: str | None,
    confidence: str,
) -> Path | None:
    note_path = _promoted_note_path(config, project_id, memory_kind, title, entry.source_id)
    if note_path is None:
        return None
    note_path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = _promoted_note_frontmatter(
        entry,
        project_id=project_id,
        workspace_id=workspace_id,
        repo_root=repo_root,
        memory_kind=memory_kind,
        title=title,
        confidence=confidence,
    )
    atomic_write_text(
        note_path,
        _promoted_note_body(
            entry,
            frontmatter,
            summary=summary,
            promotion_reason=promotion_reason,
            reviewer=reviewer,
        ),
    )
    return note_path


def _conversation_index_path(config: SupervisorConfig, project_id: str) -> Path | None:
    wiki_root = _obsidian_wiki_root(config)
    if wiki_root is None:
        return None
    return wiki_root / "Sources" / project_id / "conversation-index.md"


def _write_conversation_index(config: SupervisorConfig, *, project_id: str, workspace_id: str | None, repo_root: str | None, entries: list[SourceManifestEntry]) -> Path | None:
    index_path = _conversation_index_path(config, project_id)
    if index_path is None:
        return None
    index_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        "| source_id | title | review | status | bytes | source |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for entry in sorted(entries, key=lambda item: (item.source_published_at or "", item.source_id)):
        title = (entry.source_title or entry.original_name or entry.source_id).replace("|", "\\|")
        source_name = (entry.original_name or Path(entry.source_uri).name).replace("|", "\\|")
        source_link = f"[`{entry.source_id}`](./{entry.source_id}.md)"
        rows.append(
            f"| {source_link} | {title} | `{entry.review_status}` | `{entry.status}` | {entry.size_bytes} | `{source_name}` |"
        )
    frontmatter = {
        "title": "Conversation Source Index",
        "scope": "project",
        "project_id": project_id,
        "workspace_id": workspace_id,
        "repo_root": repo_root,
        "memory_kind": "source",
        "status": "current",
        "review_status": "reviewed",
        "confidence": "high",
        "evidence_class": "operational_entrypoint",
        "source_type": "conversation",
        "updated": now_local_iso(),
    }
    body = (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()}\n---\n\n"
        "# Conversation Source Index\n\n"
        "This index lists Codex session transcript source notes for the project.\n"
        "Rows are source references, not verified project facts.\n\n"
        + "\n".join(rows)
        + "\n"
    )
    atomic_write_text(index_path, body)
    return index_path


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
    is_directory = resolved.is_dir()
    if is_directory:
        source_type = "directory"
        sha256 = _sha256_directory_registration(resolved)
        source_id = _source_id_from_directory_path(resolved)
        size_bytes = 0
        content_type = "inode/directory"
    else:
        sha256 = _sha256_file(resolved)
        source_id = _source_id_from_hash(sha256)
        size_bytes = resolved.stat().st_size
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    warnings: list[str] = []
    if is_directory:
        warnings.append("DIRECTORY_SOURCE_REGISTERED_MANIFEST_ONLY")
    if source_type not in _LIGHTWEIGHT_SOURCE_TYPES:
        warnings.append("SOURCE_TYPE_HEAVY_OR_UNSUPPORTED_FOR_PHASE_3A_MANIFEST_ONLY")
    if not is_directory and size_bytes > _MAX_LIGHTWEIGHT_SOURCE_SIZE:
        warnings.append("SOURCE_FILE_EXCEEDS_LIGHTWEIGHT_PHASE_SIZE_LIMIT")
    review_status: ReviewStatus = "pending" if privacy in _REVIEW_REQUIRED_PRIVACY else "not_required"
    redaction_status = "pending" if privacy in _REVIEW_REQUIRED_PRIVACY else "not_required"
    source_owner = "customer" if privacy == "customer" else "project"
    entry = SourceManifestEntry(
        source_id=source_id,
        source_type=source_type,
        content_type=content_type,
        source_uri=normalize_windows_path(resolved),
        raw_storage_uri=normalize_windows_path(resolved),
        original_name=resolved.name,
        source_title=resolved.stem,
        source_accessed_at=now_local_iso(),
        project_id=identity.project_id if scope == "project" else None,
        workspace_id=identity.workspace_id if scope in {"project", "workspace"} else None,
        repo_root=identity.repo_root,
        scope=scope,
        source_scope_reason="project directory registration" if is_directory else "initial source-ingest registration",
        sha256=sha256,
        size_bytes=size_bytes,
        privacy=privacy,
        source_owner=source_owner,
        extraction_status="not_supported" if is_directory else "not_started",
        redaction_status=redaction_status,
        review_status=review_status,
        retention_policy="preserve directory in place; manifest records project root only" if is_directory else None,
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
    index, entry = found
    warnings: list[str] = []
    review_required = entry.privacy in _REVIEW_REQUIRED_PRIVACY
    if review_required and entry.review_status != "reviewed":
        warnings.append("SOURCE_REVIEW_REQUIRED_BEFORE_COMPILE")
    if entry.status in {"quarantined", "rejected"}:
        warnings.append("SOURCE_STATUS_BLOCKS_COMPILE")
    if entry.source_type not in _LIGHTWEIGHT_SOURCE_TYPES:
        warnings.append("SOURCE_TYPE_COMPILE_NOT_IMPLEMENTED_FOR_HEAVY_PHASE")
    planned_note_path: str | None = None
    note_path = _source_note_path(config, identity.project_id, entry.source_id)
    if note_path is not None:
        planned_note_path = normalize_windows_path(note_path)
    frontmatter = _source_note_frontmatter(
        entry,
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
    )
    compiled = False
    if not dry_run and note_path is not None and "SOURCE_STATUS_BLOCKS_COMPILE" not in warnings and "SOURCE_TYPE_COMPILE_NOT_IMPLEMENTED_FOR_HEAVY_PHASE" not in warnings:
        written = _write_source_note(config, entry, project_id=identity.project_id, workspace_id=identity.workspace_id, repo_root=identity.repo_root)
        if written is not None:
            note_ref = normalize_windows_path(written)
            compiled_into = list(dict.fromkeys([*entry.compiled_into, note_ref]))
            manifest.entries[index] = entry.model_copy(update={"compiled_into": compiled_into, "status": "compiled"})
            _save_source_manifest(identity.project_id, manifest)
            compiled = True
    return SourceCompileResult(
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        manifest_path=normalize_windows_path(_source_manifest_path(identity.project_id)),
        dry_run=dry_run,
        compiled=compiled,
        planned_note_path=planned_note_path,
        frontmatter=frontmatter,
        warnings=warnings,
    )


def source_promote(
    config: SupervisorConfig,
    repo_root: Path,
    source_id: str,
    *,
    memory_kind: SourcePromoteKind,
    title: str,
    summary: str,
    promotion_reason: str,
    reviewer: str | None = None,
    confidence: str = "medium",
    dry_run: bool = True,
) -> SourcePromoteResult:
    """Promote a reviewed source into durable project-scoped wiki knowledge."""

    if memory_kind not in _PROMOTION_DIRS:
        raise ValueError(f"Unsupported promotion memory_kind: {memory_kind}")
    identity = build_identity(repo_root)
    manifest = _load_source_manifest(identity.project_id)
    found = _find_entry(manifest, source_id)
    if found is None:
        raise KeyError(f"Source id not found in source manifest: {source_id}")
    index, entry = found
    warnings: list[str] = []
    title = title.strip()
    summary = summary.strip()
    promotion_reason = promotion_reason.strip()
    if not title:
        warnings.append("SOURCE_PROMOTE_TITLE_REQUIRED")
        title = f"Promoted source {source_id}"
    if not summary:
        warnings.append("SOURCE_PROMOTE_SUMMARY_REQUIRED")
    if not promotion_reason:
        warnings.append("SOURCE_PROMOTE_REASON_REQUIRED")
    if entry.review_status != "reviewed":
        warnings.append("SOURCE_REVIEW_REQUIRED_BEFORE_PROMOTE")
    if entry.status in {"quarantined", "rejected"}:
        warnings.append("SOURCE_STATUS_BLOCKS_PROMOTE")
    if confidence not in {"high", "medium", "low"}:
        warnings.append("SOURCE_PROMOTE_INVALID_CONFIDENCE")
        confidence = "medium"

    note_path = _promoted_note_path(config, identity.project_id, memory_kind, title, entry.source_id)
    planned_note_path = normalize_windows_path(note_path) if note_path is not None else None
    frontmatter = _promoted_note_frontmatter(
        entry,
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        memory_kind=memory_kind,
        title=title,
        confidence=confidence,
    )

    blocking_warnings = {
        "SOURCE_PROMOTE_SUMMARY_REQUIRED",
        "SOURCE_PROMOTE_REASON_REQUIRED",
        "SOURCE_REVIEW_REQUIRED_BEFORE_PROMOTE",
        "SOURCE_STATUS_BLOCKS_PROMOTE",
    }
    promoted = False
    if not dry_run and note_path is not None and not any(warning in blocking_warnings for warning in warnings):
        written = _write_promoted_note(
            config,
            entry,
            project_id=identity.project_id,
            workspace_id=identity.workspace_id,
            repo_root=identity.repo_root,
            memory_kind=memory_kind,
            title=title,
            summary=summary,
            promotion_reason=promotion_reason,
            reviewer=reviewer,
            confidence=confidence,
        )
        if written is not None:
            written_ref = normalize_windows_path(written)
            compiled_into = list(dict.fromkeys([*entry.compiled_into, written_ref]))
            manifest.entries[index] = entry.model_copy(update={"compiled_into": compiled_into, "status": "promoted"})
            _save_source_manifest(identity.project_id, manifest)
            promoted = True

    return SourcePromoteResult(
        project_id=identity.project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        manifest_path=normalize_windows_path(_source_manifest_path(identity.project_id)),
        dry_run=dry_run,
        promoted=promoted,
        source_id=source_id,
        memory_kind=memory_kind,
        planned_note_path=planned_note_path,
        frontmatter=frontmatter,
        warnings=warnings,
    )


def codex_session_compile(
    config: SupervisorConfig,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
    limit: int | None = None,
    force: bool = False,
) -> CodexSessionCompileReport:
    """Create Obsidian source notes for registered Codex conversation sources."""

    scanned = 0
    planned = 0
    compiled = 0
    skipped = 0
    warnings: list[str] = []
    summaries: dict[str, dict[str, object]] = {}
    remaining = limit
    manifest_paths = sorted(supervisor_projects_root().glob("*/source_manifest.yaml"))
    for manifest_path in manifest_paths:
        current_project_id = manifest_path.parent.name
        if project_id and current_project_id != project_id:
            continue
        manifest = _load_source_manifest(current_project_id)
        changed = False
        conversation_entries = [entry for entry in manifest.entries if entry.source_type == "conversation"]
        if not conversation_entries:
            continue
        project_entries_for_index: list[SourceManifestEntry] = []
        for index, entry in enumerate(conversation_entries):
            if remaining is not None and remaining <= 0:
                break
            scanned += 1
            summary = summaries.setdefault(
                current_project_id,
                {
                    "project_id": current_project_id,
                    "workspace_id": entry.workspace_id,
                    "repo_root": entry.repo_root,
                    "scanned": 0,
                    "planned": 0,
                    "compiled": 0,
                    "skipped": 0,
                    "index_path": normalize_windows_path(_conversation_index_path(config, current_project_id)) if _conversation_index_path(config, current_project_id) else None,
                },
            )
            summary["scanned"] = int(summary["scanned"]) + 1
            note_path = _source_note_path(config, current_project_id, entry.source_id)
            note_ref = normalize_windows_path(note_path) if note_path else None
            already_compiled = bool(note_ref and note_ref in entry.compiled_into and note_path and note_path.exists())
            if already_compiled and not force:
                skipped += 1
                summary["skipped"] = int(summary["skipped"]) + 1
                project_entries_for_index.append(entry)
                if remaining is not None:
                    remaining -= 1
                continue
            planned += 1
            summary["planned"] = int(summary["planned"]) + 1
            project_entries_for_index.append(entry)
            if not dry_run and note_path is not None:
                written = _write_source_note(
                    config,
                    entry,
                    project_id=current_project_id,
                    workspace_id=entry.workspace_id,
                    repo_root=entry.repo_root,
                )
                if written is not None:
                    written_ref = normalize_windows_path(written)
                    compiled_into = list(dict.fromkeys([*entry.compiled_into, written_ref]))
                    manifest_index = next((i for i, item in enumerate(manifest.entries) if item.source_id == entry.source_id), None)
                    if manifest_index is not None:
                        manifest.entries[manifest_index] = entry.model_copy(update={"compiled_into": compiled_into, "status": "compiled"})
                        project_entries_for_index[-1] = manifest.entries[manifest_index]
                        changed = True
                    compiled += 1
                    summary["compiled"] = int(summary["compiled"]) + 1
            if remaining is not None:
                remaining -= 1
        if not dry_run:
            if project_entries_for_index:
                index_path = _write_conversation_index(
                    config,
                    project_id=current_project_id,
                    workspace_id=str(summaries[current_project_id].get("workspace_id") or ""),
                    repo_root=str(summaries[current_project_id].get("repo_root") or ""),
                    entries=project_entries_for_index if limit is not None else conversation_entries,
                )
                if index_path is None:
                    warnings.append(f"CODEX_SESSION_COMPILE_NO_OBSIDIAN_ROOT: {current_project_id}")
            if changed:
                _save_source_manifest(current_project_id, manifest)
        if remaining is not None and remaining <= 0:
            break

    project_summaries = [
        CodexSessionCompileProjectSummary.model_validate(summary)
        for summary in sorted(summaries.values(), key=lambda item: str(item["project_id"]))
    ]
    return CodexSessionCompileReport(
        dry_run=dry_run,
        project_id=project_id,
        scanned_sources=scanned,
        planned=planned,
        compiled=compiled,
        skipped=skipped,
        project_summaries=project_summaries,
        warnings=warnings,
    )
