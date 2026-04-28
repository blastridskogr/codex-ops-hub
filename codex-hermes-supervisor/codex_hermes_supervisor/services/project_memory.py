"""Existing project memory onboarding and refresh services."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import yaml

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import build_identity, normalize_windows_path
from codex_hermes_supervisor.core.locks import LockFileError, WorkspaceLock, now_local_iso
from codex_hermes_supervisor.core.paths import supervisor_projects_root, user_home
from codex_hermes_supervisor.integrations.hermes import append_direct_memory, write_outbox_note
from codex_hermes_supervisor.integrations.obsidian import initialize_obsidian_vault, write_wiki_note
from codex_hermes_supervisor.integrations.qmd import qmd_search
from codex_hermes_supervisor.schemas.project_memory import (
    CompactSummaryRecord,
    MemoryImportPreview,
    MemoryImportResult,
    MemoryContextPack,
    MemoryLookupResult,
    ProjectMemoryBootstrapResult,
    MemoryRefreshResult,
    MemorySearchHit,
    MemorySearchResult,
    MemoryStatusReport,
    ProjectMemoryManifest,
    ProjectOutboxItem,
    ProjectRegistrationResult,
    ProjectRegistry,
    ProjectRegistryEntry,
    ProjectSourceItem,
    ProjectWikiItem,
    StalenessRecord,
    VectorIndexStatus,
    WorkstreamCandidate,
)
from codex_hermes_supervisor.schemas.wiki import WikiNoteInput
from codex_hermes_supervisor.security.secret_scan import SecretScanError, ensure_safe_text

_TOP_LEVEL_FILES = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "docker-compose.yml",
]
_RECURSIVE_DIRS = ["docs", ".github/workflows", "tests", "scripts"]
_EXCLUDE_PARTS = {"node_modules", "dist", "build", "coverage", ".git", ".venv", "venv", "__pycache__"}
_EXCLUDE_NAMES = {".env", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
_MEMORY_LOOKUP_DEFAULT_TIMEOUT_SECONDS = 55.0
_TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".py",
    ".ps1",
    ".sh",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
}
_MAX_SOURCE_SIZE = 128 * 1024
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_WORKSTREAM_ALIASES: dict[str, tuple[str, ...]] = {
    "codex-hermes-stack": (
        "codex-hermes",
        "hermes",
        "하네스",
        "supervisor",
        "qmd",
        "vector",
        "obsidian",
        "llm wiki",
        "공식앱",
        "공식 앱",
        "공식하네스",
        "공식 하네스",
        "install-global",
        "install-project",
        "memory",
        "기억",
    ),
    "telegram-runtime": (
        "telegram",
        "텔레그램",
        "bridge",
        "브리지",
        "session",
        "codex_session",
        "native",
        "runtime",
    ),
    "appserver-telegram-rnd": (
        "appserver",
        "app-server",
        "app server",
        "json-rpc",
        "telegram-bridge",
        "parity",
        "integrated",
        "productization",
    ),
}


@dataclass(frozen=True)
class _SourceRecord:
    rel_path: str
    sha256: str
    text: str


def _projects_root() -> Path:
    root = supervisor_projects_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _registry_path() -> Path:
    return _projects_root() / "project_registry.yaml"


def _project_dir(project_id: str) -> Path:
    path = _projects_root() / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(project_id: str) -> Path:
    return _project_dir(project_id) / "memory_manifest.yaml"


def _project_yaml_path(project_id: str) -> Path:
    return _project_dir(project_id) / "project.yaml"


def _import_log_path(project_id: str) -> Path:
    return _project_dir(project_id) / "import_log.md"


def _sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _normalize_rel(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _load_registry() -> ProjectRegistry:
    path = _registry_path()
    if not path.exists():
        return ProjectRegistry()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProjectRegistry.model_validate(data)


def _save_registry(registry: ProjectRegistry) -> Path:
    path = _registry_path()
    atomic_write_text(path, yaml.safe_dump(registry.model_dump(mode="python"), sort_keys=False, allow_unicode=False))
    return path


def _load_manifest(project_id: str) -> ProjectMemoryManifest:
    path = _manifest_path(project_id)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProjectMemoryManifest.model_validate(data)


def _save_manifest(manifest: ProjectMemoryManifest) -> Path:
    path = _manifest_path(manifest.project_id)
    atomic_write_text(path, yaml.safe_dump(manifest.model_dump(mode="python"), sort_keys=False, allow_unicode=False))
    return path


def _append_import_log(project_id: str, line: str) -> Path:
    path = _import_log_path(project_id)
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Import Log\n\n"
    if not existing.endswith("\n"):
        existing += "\n"
    existing += f"- {now_local_iso()} {line}\n"
    atomic_write_text(path, existing)
    return path


def _register_entry(registry: ProjectRegistry, entry: ProjectRegistryEntry) -> bool:
    for index, existing in enumerate(registry.projects):
        if existing.project_id == entry.project_id:
            registry.projects[index] = entry
            return False
    registry.projects.append(entry)
    return True


def register_project(repo_root: Path, *, name: str | None = None, aliases: Iterable[str] = ()) -> ProjectRegistrationResult:
    repo_root = repo_root.resolve()
    identity = build_identity(repo_root)
    registry = _load_registry()
    existing_entry = next((item for item in registry.projects if item.project_id == identity.project_id), None)
    display_name = name or (existing_entry.display_name if existing_entry else repo_root.name)
    project_dir = _project_dir(identity.project_id)
    merged_aliases = list(dict.fromkeys([*(existing_entry.aliases if existing_entry else []), *list(aliases)]))
    merged_remotes = list(dict.fromkeys([*(existing_entry.repo_remotes if existing_entry else []), *([identity.git_remote] if identity.git_remote else [])]))
    merged_workspaces = list(dict.fromkeys([*(existing_entry.known_workspaces if existing_entry else []), identity.repo_root]))

    entry = ProjectRegistryEntry(
        project_id=identity.project_id,
        display_name=display_name,
        aliases=merged_aliases,
        repo_remotes=merged_remotes,
        known_workspaces=merged_workspaces,
        project_root=identity.repo_root,
        manifest_path=normalize_windows_path(_manifest_path(identity.project_id)),
        project_note=existing_entry.project_note if existing_entry else None,
        status=existing_entry.status if existing_entry else "draft",
        last_refreshed_at=now_local_iso(),
    )
    created = _register_entry(registry, entry)
    _save_registry(registry)
    atomic_write_text(
        _project_yaml_path(identity.project_id),
        yaml.safe_dump(
            {
                "project_id": identity.project_id,
                "display_name": display_name,
                "project_root": identity.repo_root,
                "repo_remote": identity.git_remote,
                "git_common_dir": identity.git_common_dir,
                "known_workspaces": [identity.repo_root],
            },
            sort_keys=False,
            allow_unicode=False,
        ),
    )
    return ProjectRegistrationResult(
        project_id=identity.project_id,
        project_root=identity.repo_root,
        project_dir=normalize_windows_path(project_dir),
        registry_path=normalize_windows_path(_registry_path()),
        created=created,
    )


def _describe_project(repo_root: Path, *, name: str | None = None) -> tuple[str, str, str]:
    identity = build_identity(repo_root)
    existing_name: str | None = None
    if name is None:
        try:
            existing_name = show_project(identity.project_id).display_name
        except FileNotFoundError:
            existing_name = None
    display_name = name or existing_name or repo_root.name
    return identity.project_id, display_name, identity.repo_root


def list_projects() -> ProjectRegistry:
    return _load_registry()


def show_project(project_id: str) -> ProjectRegistryEntry:
    registry = _load_registry()
    for entry in registry.projects:
        if entry.project_id == project_id:
            return entry
    raise FileNotFoundError(f"Unknown project_id: {project_id}")


def _candidate_paths(repo_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in _TOP_LEVEL_FILES:
        path = repo_root / name
        if path.exists() and path.is_file():
            candidates.append(path)

    for dirname in _RECURSIVE_DIRS:
        base = repo_root / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _EXCLUDE_PARTS for part in path.parts):
                continue
            if path.name in _EXCLUDE_NAMES:
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if path.stat().st_size > _MAX_SOURCE_SIZE:
                continue
            candidates.append(path)

    deduped: dict[str, Path] = {}
    for path in candidates:
        deduped[str(path.resolve())] = path
    return sorted(deduped.values(), key=lambda item: str(item).lower())


def _scan_sources(repo_root: Path) -> list[_SourceRecord]:
    sources: list[_SourceRecord] = []
    for path in _candidate_paths(repo_root):
        rel_path = _normalize_rel(path, repo_root)
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            ensure_safe_text(text)
        except SecretScanError:
            continue
        sources.append(_SourceRecord(rel_path=rel_path, sha256=_sha256_file(path), text=text))
    return sources


def _extract_readme_summary(repo_root: Path) -> str:
    readme = repo_root / "README.md"
    if not readme.exists():
        return ""
    text = readme.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff").replace("`n", "\n")
    lines = [line.strip() for line in text.splitlines()]
    content = [line for line in lines if line and not line.startswith("#")]
    return " ".join(content[:3]).strip()


def _extract_test_commands(repo_root: Path) -> list[str]:
    commands: list[str] = []
    package_json = repo_root / "package.json"
    if package_json.exists():
        text = package_json.read_text(encoding="utf-8", errors="replace")
        if "\"test\"" in text:
            commands.append("npm test")
    if (repo_root / "pyproject.toml").exists():
        commands.append("pytest")
    if (repo_root / "Cargo.toml").exists():
        commands.append("cargo test")
    if (repo_root / "go.mod").exists():
        commands.append("go test ./...")
    return commands


def _build_compact_summary(repo_root: Path, display_name: str, sources: list[_SourceRecord]) -> str:
    summary_parts: list[str] = []
    readme_summary = _extract_readme_summary(repo_root)
    if readme_summary:
        summary_parts.append(readme_summary)
    else:
        summary_parts.append(f"{display_name} project imported into the Codex-Hermes harness.")

    commands = _extract_test_commands(repo_root)
    if commands:
        summary_parts.append("Test commands: " + ", ".join(commands))

    docs_present = any(source.rel_path.startswith("docs/") for source in sources)
    tests_present = any(source.rel_path.startswith("tests/") for source in sources)
    if docs_present:
        summary_parts.append("Documentation sources were imported.")
    if tests_present:
        summary_parts.append("Repository tests were included in onboarding sources.")

    return " ".join(part.strip() for part in summary_parts if part.strip()).strip()


def _project_note_input(repo_root: Path, task_id: str, display_name: str, compact_summary: str, sources: list[_SourceRecord]) -> WikiNoteInput:
    evidence = [f"Imported source: {source.rel_path}" for source in sources[:8]]
    links = [source.rel_path for source in sources[:8]]
    next_steps = ["Review project note and commit compact memory to Hermes."]
    commands = _extract_test_commands(repo_root)
    if commands:
        next_steps.insert(0, "Suggested test commands: " + ", ".join(commands))
    return WikiNoteInput(
        repo_root=str(repo_root),
        task_id=task_id,
        type="project",
        title=display_name,
        summary=compact_summary,
        evidence=evidence,
        links=links,
        next_steps=next_steps,
    )


def memory_import_preview(config: SupervisorConfig, repo_root: Path, *, mode: str = "baseline") -> MemoryImportPreview:
    repo_root = repo_root.resolve()
    project_id, display_name, normalized_root = _describe_project(repo_root)
    sources = _scan_sources(repo_root)
    compact_summary = _build_compact_summary(repo_root, display_name, sources)
    project_note_path: str | None = None
    if config.obsidian.enabled and config.obsidian_root is not None:
        initialize_obsidian_vault(config.obsidian_root, config.obsidian.wiki_root)
        project_note_path = normalize_windows_path(config.obsidian_root / config.obsidian.wiki_root / "Projects" / f"{display_name.lower().replace(' ', '-')}.md")

    return MemoryImportPreview(
        project_id=project_id,
        display_name=display_name,
        repo_root=normalized_root,
        mode=mode,
        sources=[
            ProjectSourceItem(path=source.rel_path, sha256=source.sha256, status="draft")
            for source in sources
        ],
        compact_summary=compact_summary,
        project_note_path=project_note_path,
        dry_run=True,
    )


def memory_import_apply(config: SupervisorConfig, repo_root: Path, *, mode: str = "baseline") -> MemoryImportResult:
    repo_root = repo_root.resolve()
    registration = register_project(repo_root)
    display_name = show_project(registration.project_id).display_name
    sources = _scan_sources(repo_root)
    compact_summary = _build_compact_summary(repo_root, display_name, sources)
    ensure_safe_text(compact_summary, *[source.text[:2000] for source in sources[:8]])

    manifest = ProjectMemoryManifest(
        project_id=registration.project_id,
        display_name=display_name,
        repo_root=registration.project_root,
        status="draft",
        created_at=now_local_iso(),
        updated_at=now_local_iso(),
        sources=[ProjectSourceItem(path=source.rel_path, sha256=source.sha256, status="draft") for source in sources],
        compact_summary=CompactSummaryRecord(status="draft", text=compact_summary),
        staleness=StalenessRecord(stale=False, stale_reasons=[]),
    )

    project_note_path: str | None = None
    if config.obsidian.enabled and config.obsidian_root is not None:
        initialize_obsidian_vault(config.obsidian_root, config.obsidian.wiki_root)
        note_input = _project_note_input(repo_root, f"{registration.project_id}-baseline", display_name, compact_summary, sources)
        note_data = write_wiki_note(
            config.obsidian_root,
            config.obsidian.wiki_root,
            registration.project_id,
            note_input,
            obsidian_config=config.obsidian,
        )
        if note_data.path and note_data.wikilink:
            project_note_path = note_data.path
            manifest.wiki_notes.append(ProjectWikiItem(type="project", path=note_data.path, wikilink=note_data.wikilink, status="draft"))

    manifest_path = _save_manifest(manifest)

    registry = _load_registry()
    entry = show_project(registration.project_id)
    entry.manifest_path = normalize_windows_path(manifest_path)
    entry.project_note = manifest.wiki_notes[0].wikilink if manifest.wiki_notes else None
    entry.status = "draft"
    entry.last_refreshed_at = now_local_iso()
    _register_entry(registry, entry)
    _save_registry(registry)
    _append_import_log(registration.project_id, "Imported project memory baseline as draft.")

    return MemoryImportResult(
        project_id=registration.project_id,
        display_name=display_name,
        manifest_path=normalize_windows_path(manifest_path),
        project_note_path=project_note_path,
        compact_summary=compact_summary,
        dry_run=False,
    )


def memory_review(project_id: str) -> MemoryStatusReport:
    manifest = _load_manifest(project_id)
    project_note = manifest.wiki_notes[0].wikilink if manifest.wiki_notes else None
    return MemoryStatusReport(
        project_id=manifest.project_id,
        display_name=manifest.display_name,
        status=manifest.status,
        manifest_path=normalize_windows_path(_manifest_path(project_id)),
        project_note=project_note,
        compact_summary=manifest.compact_summary.text,
        stale=manifest.staleness.stale,
        stale_reasons=manifest.staleness.stale_reasons,
    )


def memory_commit(config: SupervisorConfig, project_id: str) -> MemoryStatusReport:
    manifest = _load_manifest(project_id)
    pointer = manifest.wiki_notes[0].wikilink if manifest.wiki_notes else None
    if config.hermes.mode == "outbox":
        outbox_path, content_hash = write_outbox_note(
            config.hermes_outbox_root,
            note_type="project_baseline",
            task_id=f"{project_id}-project-baseline",
            project_id=manifest.project_id,
            workspace_id=manifest.project_id,
            repo_root=manifest.repo_root,
            scope="project",
            memory_kind="project_fact",
            source_tool="memory_import",
            title=f"Project Baseline: {manifest.display_name}",
            compact_summary=manifest.compact_summary.text,
            pointer=pointer,
        )
        outbox_status = "pending_import"
    else:
        content = manifest.compact_summary.text
        if pointer:
            content = f"{content}\n\n{pointer}"
        outbox_path = Path(append_direct_memory(config, content, target="memory").path)
        content_hash = _sha256_text(content)
        outbox_status = "imported"
    manifest.status = "reviewed"
    manifest.updated_at = now_local_iso()
    manifest.compact_summary.status = "reviewed"
    manifest.hermes_outbox = [ProjectOutboxItem(path=normalize_windows_path(outbox_path), status=outbox_status, content_hash=content_hash)]
    _save_manifest(manifest)

    registry = _load_registry()
    entry = show_project(project_id)
    entry.status = "reviewed"
    entry.project_note = pointer
    entry.last_refreshed_at = now_local_iso()
    _register_entry(registry, entry)
    _save_registry(registry)
    _append_import_log(project_id, "Committed reviewed compact summary to Hermes outbox.")
    return memory_review(project_id)


def memory_status(project_id: str) -> MemoryStatusReport:
    return memory_review(project_id)


def _bootstrap_project_note(project_id: str, title: str, body: str, note_type: str) -> str:
    return (
        "---\n"
        f"scope: project\n"
        f"project_id: {project_id}\n"
        f"type: {note_type}\n"
        f"created: {now_local_iso()}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body.rstrip()}\n"
    )


def project_memory_bootstrap(config: SupervisorConfig, repo_root: Path, *, dry_run: bool = True) -> ProjectMemoryBootstrapResult:
    """Create deterministic project-scoped CodexWiki entrypoints for an empty project."""

    repo_root = repo_root.resolve()
    identity = build_identity(repo_root)
    if config.obsidian_root is None:
        return ProjectMemoryBootstrapResult(
            project_id=identity.project_id,
            repo_root=identity.repo_root,
            dry_run=dry_run,
            warnings=["Obsidian CodexWiki root is not configured; project memory bootstrap cannot create Markdown backend files."],
        )

    wiki_dir = config.obsidian_root / config.obsidian.wiki_root
    targets = {
        wiki_dir / "_index.md": _bootstrap_project_note(
            identity.project_id,
            "CodexWiki Index",
            "Global CodexWiki index. Project-specific status/log files remain scoped by project_id.",
            "index",
        ),
        wiki_dir / "_schema" / "WIKI_SCHEMA.md": _bootstrap_project_note(
            identity.project_id,
            "CodexWiki Schema",
            "Notes use frontmatter with scope, project_id, type, and created fields.",
            "schema",
        ),
        wiki_dir / "Projects" / identity.project_id / "status.md": _bootstrap_project_note(
            identity.project_id,
            f"Project Status: {repo_root.name}",
            "Initial project memory bootstrap. Replace this with current status after the first supervised task.",
            "project_status",
        ),
        wiki_dir / "Tasks" / identity.project_id / "log.md": _bootstrap_project_note(
            identity.project_id,
            f"Task Log: {repo_root.name}",
            "- Initial project memory bootstrap.\n",
            "project_log",
        ),
        wiki_dir / "Sources" / "_manifest.md": _bootstrap_project_note(
            identity.project_id,
            "Source Manifest",
            "Track raw/source provenance here. Do not paste raw secrets, logs, PDFs, workbooks, or screenshots.",
            "source_manifest",
        ),
    }
    planned = [normalize_windows_path(path) for path in targets]
    created: list[str] = []
    existing: list[str] = []
    if not dry_run:
        initialize_obsidian_vault(config.obsidian_root, config.obsidian.wiki_root)
    for path, content in targets.items():
        if path.exists():
            existing.append(normalize_windows_path(path))
            continue
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        created.append(normalize_windows_path(path))
    return ProjectMemoryBootstrapResult(
        project_id=identity.project_id,
        repo_root=identity.repo_root,
        dry_run=dry_run,
        created_paths=created,
        existing_paths=existing,
        planned_paths=planned,
    )


def load_project_context(project_id: str) -> MemoryStatusReport | None:
    try:
        return memory_review(project_id)
    except FileNotFoundError:
        return None


def _manifest_source_map(manifest: ProjectMemoryManifest) -> dict[str, str]:
    return {source.path: source.sha256 for source in manifest.sources}


def memory_refresh(config: SupervisorConfig, repo_root: Path, *, dry_run: bool = True) -> MemoryRefreshResult:
    repo_root = repo_root.resolve()
    identity = build_identity(repo_root)
    manifest = _load_manifest(identity.project_id)
    current_sources = _scan_sources(repo_root)
    current_map = {source.rel_path: source.sha256 for source in current_sources}
    old_map = _manifest_source_map(manifest)

    changed_sources = sorted(
        {
            path
            for path in set(current_map) | set(old_map)
            if current_map.get(path) != old_map.get(path)
        }
    )
    stale = bool(changed_sources)
    stale_reasons = [f"Source changed: {path}" for path in changed_sources]

    if not dry_run:
        manifest.staleness = StalenessRecord(stale=stale, stale_reasons=stale_reasons)
        manifest.status = "stale" if stale else manifest.status
        manifest.updated_at = now_local_iso()
        _save_manifest(manifest)
        registry = _load_registry()
        entry = show_project(identity.project_id)
        entry.status = "stale" if stale else entry.status
        entry.last_refreshed_at = now_local_iso()
        _register_entry(registry, entry)
        _save_registry(registry)
        _append_import_log(identity.project_id, "Refreshed project memory manifest from repository sources.")

    return MemoryRefreshResult(
        project_id=manifest.project_id,
        display_name=manifest.display_name,
        stale=stale,
        stale_reasons=stale_reasons,
        changed_sources=changed_sources,
        dry_run=dry_run,
    )


def memory_forget(project_id: str) -> MemoryStatusReport:
    manifest = _load_manifest(project_id)
    manifest.status = "rejected"
    manifest.updated_at = now_local_iso()
    _save_manifest(manifest)
    registry = _load_registry()
    entry = show_project(project_id)
    entry.status = "rejected"
    entry.last_refreshed_at = now_local_iso()
    _register_entry(registry, entry)
    _save_registry(registry)
    _append_import_log(project_id, "Marked project memory as rejected.")
    return memory_review(project_id)


def _iter_search_files(
    project_id: str | None = None,
    *,
    outbox_root: Path | None = None,
    obsidian_root: Path | None = None,
    wiki_root: str = "CodexWiki",
) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add_file(kind: str, path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        files.append((kind, path))

    projects_root = _projects_root()
    if project_id:
        manifest = _manifest_path(project_id)
        if manifest.exists():
            add_file("manifest", manifest)
        project_dir = _project_dir(project_id)
        for path in sorted(project_dir.glob("*.md")):
            add_file("note", path)
    else:
        for manifest in sorted(projects_root.glob("*/memory_manifest.yaml")):
            add_file("manifest", manifest)
            for path in sorted(manifest.parent.glob("*.md")):
                add_file("note", path)

    effective_outbox_root = outbox_root or (user_home() / ".codex-hermes" / "hermes_outbox")
    for path in sorted(effective_outbox_root.rglob("*.md")):
        if project_id and project_id not in path.read_text(encoding="utf-8", errors="replace"):
            continue
        add_file("outbox", path)

    if obsidian_root is not None:
        wiki_path = obsidian_root / wiki_root
        if wiki_path.exists():
            for path in sorted(wiki_path.rglob("*.md")):
                if project_id and project_id not in path.read_text(encoding="utf-8", errors="replace"):
                    continue
                add_file("wiki", path)
    return files


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _semantic_score(query: str, text: str) -> float:
    query_tokens = _tokenize(query)
    doc_tokens = _tokenize(text)
    if not query_tokens or not doc_tokens:
        return 0.0

    vocab = sorted(set(query_tokens) | set(doc_tokens))
    q_counts = {token: query_tokens.count(token) for token in vocab}
    d_counts = {token: doc_tokens.count(token) for token in vocab}
    q_norm = math.sqrt(sum(value * value for value in q_counts.values()))
    d_norm = math.sqrt(sum(value * value for value in d_counts.values()))
    if q_norm == 0.0 or d_norm == 0.0:
        return 0.0
    dot = sum(q_counts[token] * d_counts[token] for token in vocab)
    return dot / (q_norm * d_norm)


def _vector_index_dir(config: SupervisorConfig) -> Path:
    path = config.cache_root / "vector_local"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _vector_index_path(config: SupervisorConfig, project_id: str | None = None) -> Path:
    scope = project_id or "global"
    return _vector_index_dir(config) / f"{scope}.json"


def _vector_lock_path(config: SupervisorConfig, project_id: str | None = None) -> Path:
    scope = project_id or "global"
    return _vector_index_dir(config) / f"{scope}.lock.json"


def _hash_token(token: str, dimensions: int) -> int:
    return int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dimensions


def _vectorize_text(text: str, *, dimensions: int) -> dict[int, float]:
    counts: dict[int, float] = {}
    for token in _tokenize(text):
        index = _hash_token(token, dimensions)
        counts[index] = counts.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values()))
    if norm == 0.0:
        return {}
    return {index: value / norm for index, value in counts.items()}


def _vector_dot(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def _source_rank_bonus(kind: str, *, vector: bool, path: str | Path | None = None) -> int:
    if kind == "wiki":
        normalized_path = str(path or "").replace("\\", "/").lower()
        bonus = 125 if vector else 5
        if "/projects/" in normalized_path:
            bonus += 125 if vector else 10
        return bonus
    if kind == "note":
        return 25 if vector else 2
    return 0


def _project_note_query_bonus(kind: str, path: Path, text: str, terms: list[str], *, semantic: bool) -> int:
    if kind != "wiki" or not terms:
        return 0
    normalized_path = str(path).replace("\\", "/").lower()
    if "/projects/" not in normalized_path:
        return 0
    lowered = text.lower()
    if not all(term in lowered for term in terms):
        return 0
    return 200 if semantic else 20


def _build_snippet(text: str, *, max_chars: int = 220) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return compact[:max_chars].strip()


def _markdown_frontmatter(text: str) -> dict[str, object]:
    if not text.startswith("---\n"):
        return {}
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _path_maps_to_project(path: Path, project_id: str | None) -> bool:
    if not project_id:
        return False
    normalized = str(path.resolve()).replace("\\", "/").lower()
    pid = project_id.lower()
    return (
        f"/{pid}/" in normalized
        or normalized.endswith(f"/{pid}.md")
        or f"/projects/{pid}/" in normalized
        or f"/tasks/{pid}/" in normalized
        or f"/decisions/{pid}/" in normalized
        or f"/bugs/{pid}/" in normalized
        or f"/workflows/{pid}/" in normalized
        or f"/sources/{pid}/" in normalized
    )


def _frontmatter_string(frontmatter: dict[str, object], key: str) -> str | None:
    value = frontmatter.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _reference_only_status(
    *,
    source_status: str | None,
    source_review_status: str | None,
    source_evidence_class: str | None,
) -> str | None:
    evidence_class = (source_evidence_class or "").lower()
    review_status = (source_review_status or "").lower()
    status = (source_status or "").lower()
    if evidence_class == "operational_entrypoint":
        return None
    if review_status == "rejected" or status == "rejected":
        return "reference_candidate_rejected"
    if status in {"stale", "superseded", "archived"}:
        return "reference_candidate_stale"
    if evidence_class == "reference_only" or review_status == "unverified" or status in {"candidate", "draft"}:
        return "reference_candidate_unverified"
    return None


def _evidence_metadata_for_source(path: Path, *, kind: str, current_project_id: str | None) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return {
            "source_read": False,
            "current_project_id": current_project_id,
            "evidence_allowed": False,
            "evidence_status": "source_unreadable",
        }

    hit_scope: str | None = None
    hit_project_id: str | None = None
    source_status: str | None = None
    source_review_status: str | None = None
    source_evidence_class: str | None = None
    source_confidence: str | None = None
    if kind == "manifest":
        try:
            manifest = ProjectMemoryManifest.model_validate(yaml.safe_load(text) or {})
            hit_scope = "project"
            hit_project_id = manifest.project_id
        except Exception:
            hit_scope = "unknown"
    else:
        frontmatter = _markdown_frontmatter(text)
        raw_scope = frontmatter.get("scope")
        raw_project_id = frontmatter.get("project_id")
        hit_scope = str(raw_scope) if raw_scope else None
        hit_project_id = str(raw_project_id) if raw_project_id else None
        source_status = _frontmatter_string(frontmatter, "status")
        source_review_status = _frontmatter_string(frontmatter, "review_status")
        source_evidence_class = _frontmatter_string(frontmatter, "evidence_class")
        source_confidence = _frontmatter_string(frontmatter, "confidence")
        if hit_project_id and hit_scope is None:
            hit_scope = "project"
        if hit_scope is None and _path_maps_to_project(path, current_project_id):
            hit_scope = "project"
            hit_project_id = current_project_id
    if hit_scope in {"global", "user"}:
        evidence_allowed = True
        evidence_status = "allowed_global"
    elif current_project_id is None:
        evidence_allowed = True
        evidence_status = "allowed_unscoped_global_search" if hit_scope is None else "allowed"
    elif hit_project_id == current_project_id:
        evidence_allowed = True
        evidence_status = "allowed_project"
    elif hit_project_id:
        evidence_allowed = False
        evidence_status = "reference_candidate_cross_project"
    else:
        evidence_allowed = False
        evidence_status = "reference_candidate_unscoped"
    reference_status = _reference_only_status(
        source_status=source_status,
        source_review_status=source_review_status,
        source_evidence_class=source_evidence_class,
    )
    if evidence_allowed and reference_status:
        evidence_allowed = False
        evidence_status = reference_status

    return {
        "source_read": True,
        "source_sha256": _sha256_file(path),
        "source_last_modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        "hit_scope": hit_scope,
        "hit_project_id": hit_project_id,
        "current_project_id": current_project_id,
        "source_status": source_status,
        "source_review_status": source_review_status,
        "source_evidence_class": source_evidence_class,
        "source_confidence": source_confidence,
        "evidence_allowed": evidence_allowed,
        "evidence_status": evidence_status,
    }


def _enrich_search_hits_with_evidence(
    hits: list[MemorySearchHit],
    *,
    current_project_id: str | None,
) -> tuple[list[MemorySearchHit], list[str]]:
    enriched: list[MemorySearchHit] = []
    cross_project = 0
    unreadable = 0
    unscoped = 0
    unverified = 0
    for hit in hits:
        metadata = _evidence_metadata_for_source(Path(hit.path), kind=hit.kind, current_project_id=current_project_id)
        status = str(metadata.get("evidence_status") or "")
        if status == "source_unreadable":
            unreadable += 1
        elif status == "reference_candidate_cross_project":
            cross_project += 1
        elif status == "reference_candidate_unscoped":
            unscoped += 1
        elif status in {"reference_candidate_unverified", "reference_candidate_stale", "reference_candidate_rejected"}:
            unverified += 1
        project_id = metadata.get("hit_project_id") or hit.project_id
        enriched.append(hit.model_copy(update={**metadata, "project_id": project_id}))
    warnings: list[str] = []
    if cross_project:
        warnings.append(f"MEMORY_SEARCH_CROSS_PROJECT_HITS_REFERENCE_ONLY: {cross_project} hit(s) were not allowed as evidence.")
    if unscoped:
        warnings.append(f"MEMORY_SEARCH_UNSCOPED_HITS_REFERENCE_ONLY: {unscoped} hit(s) were not allowed as evidence.")
    if unreadable:
        warnings.append(f"MEMORY_SEARCH_UNREADABLE_HITS_NOT_EVIDENCE: {unreadable} hit(s) could not be read as source evidence.")
    if unverified:
        warnings.append(f"MEMORY_SEARCH_UNVERIFIED_HITS_REFERENCE_ONLY: {unverified} hit(s) were unverified, stale, rejected, or reference-only.")
    return enriched, warnings


def _dedupe_hits(hits: Iterable[MemorySearchHit]) -> list[MemorySearchHit]:
    deduped: dict[str, MemorySearchHit] = {}
    for hit in hits:
        key = normalize_windows_path(Path(hit.path)) if hit.path else hit.path
        existing = deduped.get(key)
        if existing is None or (hit.evidence_allowed and not existing.evidence_allowed) or hit.score > existing.score:
            deduped[key] = hit.model_copy(update={"path": key})
    return sorted(deduped.values(), key=lambda item: (not item.evidence_allowed, -item.score, item.path.lower()))


def _hit_text_and_frontmatter(hit: MemorySearchHit) -> tuple[str, dict[str, object]]:
    try:
        text = Path(hit.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", {}
    return text, _markdown_frontmatter(text)


def _as_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _add_workstream_candidate(
    candidates: dict[str, dict[str, object]],
    workstream_id: str,
    *,
    score: int,
    reason: str,
    source_path: str | None = None,
) -> None:
    workstream_id = workstream_id.strip()
    if not workstream_id:
        return
    item = candidates.setdefault(workstream_id, {"score": 0, "reasons": [], "source_paths": []})
    item["score"] = int(item["score"]) + score
    reasons = item["reasons"]
    if isinstance(reasons, list) and reason not in reasons:
        reasons.append(reason)
    paths = item["source_paths"]
    if source_path and isinstance(paths, list) and source_path not in paths:
        paths.append(source_path)


def _infer_workstream_candidates(
    query: str,
    hits: list[MemorySearchHit],
    *,
    requested_workstream_id: str | None = None,
) -> list[WorkstreamCandidate]:
    candidates: dict[str, dict[str, object]] = {}
    if requested_workstream_id:
        _add_workstream_candidate(candidates, requested_workstream_id, score=10_000, reason="requested")

    lowered_query = query.lower()
    for workstream_id, aliases in _WORKSTREAM_ALIASES.items():
        if any(alias.lower() in lowered_query for alias in aliases):
            _add_workstream_candidate(candidates, workstream_id, score=250, reason="query_keyword")

    for hit in hits:
        if not hit.evidence_allowed:
            continue
        text, frontmatter = _hit_text_and_frontmatter(hit)
        explicit = frontmatter.get("workstream_id") or frontmatter.get("workstream")
        for value in _as_string_list(explicit):
            _add_workstream_candidate(candidates, value, score=500 + hit.score, reason="frontmatter", source_path=hit.path)

        declared_aliases = _as_string_list(frontmatter.get("workstream_aliases")) + _as_string_list(frontmatter.get("aliases"))
        search_blob = f"{hit.path}\n{hit.snippet}\n{text[:4000]}".lower()
        for alias in declared_aliases:
            if alias.lower() in lowered_query:
                for value in _as_string_list(explicit):
                    _add_workstream_candidate(candidates, value, score=200 + hit.score, reason="frontmatter_alias", source_path=hit.path)
        for workstream_id, aliases in _WORKSTREAM_ALIASES.items():
            if any(alias.lower() in search_blob for alias in aliases):
                _add_workstream_candidate(candidates, workstream_id, score=50 + min(hit.score, 200), reason="source_keyword", source_path=hit.path)

    results = [
        WorkstreamCandidate(
            workstream_id=workstream_id,
            score=int(data["score"]),
            reason=", ".join(str(item) for item in data.get("reasons", [])),
            source_paths=[str(item) for item in data.get("source_paths", [])],
        )
        for workstream_id, data in candidates.items()
    ]
    return sorted(results, key=lambda item: (-item.score, item.workstream_id))


def _context_pack_summary(hits: list[MemorySearchHit]) -> str:
    if not hits:
        return "No project-scoped or global memory evidence matched the query."
    lines: list[str] = []
    for hit in hits[:5]:
        name = Path(hit.path).name
        snippet = hit.snippet.strip() or hit.evidence_status
        lines.append(f"{hit.kind}:{name}: {snippet[:180]}")
    return " | ".join(lines)


def _unique_warnings(warnings: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(warning for warning in warnings if warning))


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 5) -> None:
    delay = 0.05
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _atomic_write_vector_payload(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    with open(tmp, "a", encoding="utf-8") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    _replace_with_retry(tmp, path)


def _write_vector_index(config: SupervisorConfig, project_id: str | None) -> VectorIndexStatus:
    lock = WorkspaceLock(
        _vector_lock_path(config, project_id),
        workspace_id=f"vector-{project_id or 'global'}",
        task_id=project_id or "global",
        operation="vector_reindex",
        timeout_seconds=config.state.lock_timeout_seconds,
        heartbeat_seconds=config.state.heartbeat_seconds,
    )
    dimensions = config.search.vector_local.dimensions
    with lock:
        documents: list[dict[str, object]] = []
        for kind, path in _iter_search_files(
            project_id,
            outbox_root=config.hermes_outbox_root,
            obsidian_root=config.obsidian_root,
            wiki_root=config.obsidian.wiki_root,
        ):
            text = path.read_text(encoding="utf-8", errors="replace")
            vector = _vectorize_text(text, dimensions=dimensions)
            if not vector:
                continue
            hit_project_id = project_id
            if kind == "manifest":
                try:
                    manifest = ProjectMemoryManifest.model_validate(yaml.safe_load(text) or {})
                    hit_project_id = manifest.project_id
                except Exception:
                    hit_project_id = project_id
            documents.append(
                {
                    "kind": kind,
                    "path": normalize_windows_path(path),
                    "project_id": hit_project_id,
                    "snippet": _build_snippet(text),
                    "vector": {str(index): value for index, value in vector.items()},
                }
            )
        index_path = _vector_index_path(config, project_id)
        _atomic_write_vector_payload(
            index_path,
            json.dumps(
                {
                    "schema_version": 1,
                    "backend": "vector_local",
                    "project_id": project_id,
                    "dimensions": dimensions,
                    "generated_at": now_local_iso(),
                    "documents": documents,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return VectorIndexStatus(
        project_id=project_id,
        index_path=normalize_windows_path(index_path),
        exists=True,
        documents=len(documents),
        dimensions=dimensions,
    )


def vector_index_status(config: SupervisorConfig, *, project_id: str | None = None) -> VectorIndexStatus:
    index_path = _vector_index_path(config, project_id)
    if not index_path.exists():
        return VectorIndexStatus(
            project_id=project_id,
            index_path=normalize_windows_path(index_path),
            exists=False,
            documents=0,
            dimensions=config.search.vector_local.dimensions,
            warnings=["Vector index has not been built yet."],
        )
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    documents = payload.get("documents") or []
    return VectorIndexStatus(
        project_id=project_id,
        index_path=normalize_windows_path(index_path),
        exists=True,
        documents=len(documents),
        dimensions=int(payload.get("dimensions", config.search.vector_local.dimensions)),
    )


def reindex_vector_local(config: SupervisorConfig, *, project_id: str | None = None) -> VectorIndexStatus:
    return _write_vector_index(config, project_id)


def _vector_search_local(
    config: SupervisorConfig,
    *,
    query: str,
    project_id: str | None,
    limit: int,
) -> MemorySearchResult:
    status = vector_index_status(config, project_id=project_id)
    warnings = list(status.warnings)
    if not status.exists:
        if config.search.vector_local.auto_reindex:
            try:
                status = reindex_vector_local(config, project_id=project_id)
            except LockFileError:
                stale = vector_index_status(config, project_id=project_id)
                if stale.exists:
                    warnings.append("VECTOR_INDEX_REBUILD_IN_PROGRESS")
                    status = stale
                else:
                    warnings.append("VECTOR_INDEX_REBUILD_IN_PROGRESS")
                    return MemorySearchResult(
                        query=query,
                        project_id=project_id,
                        mode="semantic",
                        backend="vector_local",
                        warnings=warnings,
                        hits=[],
                    )
        else:
            return MemorySearchResult(
                query=query,
                project_id=project_id,
                mode="semantic",
                backend="vector_local",
                warnings=warnings,
                hits=[],
            )

    payload = json.loads(Path(status.index_path).read_text(encoding="utf-8"))
    query_vector = _vectorize_text(query, dimensions=int(payload.get("dimensions", config.search.vector_local.dimensions)))
    hits: list[MemorySearchHit] = []
    for document in payload.get("documents", []):
        kind = str(document.get("kind", "note"))
        vector = {int(index): float(value) for index, value in (document.get("vector") or {}).items()}
        score = _vector_dot(query_vector, vector)
        if score <= 0.0:
            continue
        document_path = str(document.get("path", ""))
        ranked_score = int(round(score * 1000)) + _source_rank_bonus(kind, vector=True, path=document_path)
        hits.append(
            MemorySearchHit(
                kind=kind,
                path=document_path,
                project_id=document.get("project_id"),
                score=ranked_score,
                snippet=str(document.get("snippet") or ""),
            )
        )
    hits.sort(key=lambda item: (-item.score, item.path.lower()))
    hits, evidence_warnings = _enrich_search_hits_with_evidence(hits, current_project_id=project_id)
    warnings.extend(evidence_warnings)
    return MemorySearchResult(
        query=query,
        project_id=project_id,
        mode="semantic",
        backend="vector_local",
        warnings=warnings,
        hits=hits[:limit],
    )


def _memory_search_semantic_lite(
    query: str,
    *,
    project_id: str | None = None,
    limit: int = 10,
    mode: str = "keyword",
    outbox_root: Path | None = None,
    obsidian_root: Path | None = None,
    wiki_root: str = "CodexWiki",
) -> MemorySearchResult:
    terms = [term.lower() for term in query.split() if term.strip()]
    hits: list[MemorySearchHit] = []
    for kind, path in _iter_search_files(
        project_id,
        outbox_root=outbox_root,
        obsidian_root=obsidian_root,
        wiki_root=wiki_root,
    ):
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        if mode == "semantic":
            semantic = _semantic_score(query, text)
            if semantic <= 0.0:
                continue
            score = int(round(semantic * 1000))
            first_term = next((term for term in terms if term in lowered), None)
        else:
            score = sum(lowered.count(term) for term in terms)
            if score <= 0:
                continue
            first_term = next((term for term in terms if term in lowered), None)
        semantic_mode = mode == "semantic"
        score += _source_rank_bonus(kind, vector=semantic_mode, path=path)
        score += _project_note_query_bonus(kind, path, text, terms, semantic=semantic_mode)
        snippet = ""
        if first_term:
            index = lowered.index(first_term)
            start = max(0, index - 80)
            end = min(len(text), index + 160)
            snippet = text[start:end].replace("\n", " ").strip()
        hit_project_id = project_id
        if kind == "manifest":
            try:
                manifest = ProjectMemoryManifest.model_validate(yaml.safe_load(text) or {})
                hit_project_id = manifest.project_id
            except Exception:
                hit_project_id = project_id
        hits.append(
            MemorySearchHit(
                kind=kind,
                path=normalize_windows_path(path),
                project_id=hit_project_id,
                score=score,
                snippet=snippet,
            )
        )
    hits.sort(key=lambda item: (-item.score, item.path.lower()))
    hits, evidence_warnings = _enrich_search_hits_with_evidence(hits, current_project_id=project_id)
    normalized_mode = "semantic" if mode == "semantic" else "keyword"
    return MemorySearchResult(
        query=query,
        project_id=project_id,
        mode=normalized_mode,
        backend="semantic_lite",
        warnings=evidence_warnings,
        hits=hits[:limit],
    )


def memory_search(
    query: str,
    *,
    project_id: str | None = None,
    limit: int = 10,
    mode: str = "keyword",
    backend: str | None = None,
    config: SupervisorConfig | None = None,
) -> MemorySearchResult:
    requested_backend = backend or (config.search.backend if config else "semantic_lite")
    outbox_root = config.hermes_outbox_root if config is not None else None
    obsidian_root = config.obsidian_root if config is not None else None
    wiki_root = config.obsidian.wiki_root if config is not None else "CodexWiki"
    if requested_backend == "vector_local":
        if config is None:
            fallback = _memory_search_semantic_lite(query, project_id=project_id, limit=limit, mode="semantic" if mode in {"semantic", "hybrid"} else "keyword")
            fallback.warnings.append("Vector-local backend requires config; falling back to semantic-lite.")
            return fallback
        return _vector_search_local(config, query=query, project_id=project_id, limit=limit)
    if requested_backend == "qmd" and config is not None:
        qmd_hits, warnings = qmd_search(config, query=query, project_id=project_id, limit=limit, mode=mode)
        if qmd_hits:
            qmd_hits, evidence_warnings = _enrich_search_hits_with_evidence(qmd_hits, current_project_id=project_id)
            warnings.extend(evidence_warnings)
            return MemorySearchResult(
                query=query,
                project_id=project_id,
                mode=mode if mode in {"keyword", "semantic", "hybrid"} else "keyword",
                backend="qmd",
                warnings=warnings,
                hits=qmd_hits[:limit],
            )
        if (
            config.search.qmd.fallback_to_vector_local_if_all_hits_filtered
            and config.search.vector_local.enabled
            and any("QMD_HITS_DROPPED_OUT_OF_SCOPE" in warning for warning in warnings)
        ):
            fallback = _vector_search_local(config, query=query, project_id=project_id, limit=limit)
            fallback.warnings.extend(warnings)
            return fallback
        fallback = _memory_search_semantic_lite(
            query,
            project_id=project_id,
            limit=limit,
            mode="semantic" if mode in {"semantic", "hybrid"} else "keyword",
            outbox_root=outbox_root,
            obsidian_root=obsidian_root,
            wiki_root=wiki_root,
        )
        fallback.warnings.extend(warnings)
        return fallback

    return _memory_search_semantic_lite(
        query,
        project_id=project_id,
        limit=limit,
        mode="semantic" if mode in {"semantic", "hybrid"} else "keyword",
        outbox_root=outbox_root,
        obsidian_root=obsidian_root,
        wiki_root=wiki_root,
    )


def memory_lookup(
    query: str,
    *,
    repo_root: Path,
    config: SupervisorConfig,
    project_id: str | None = None,
    workstream_id: str | None = None,
    limit: int = 8,
    mode: str = "hybrid",
    backend: str | None = None,
    timeout_seconds: float | None = _MEMORY_LOOKUP_DEFAULT_TIMEOUT_SECONDS,
) -> MemoryLookupResult:
    """Build a scoped LLM Wiki context pack from user keywords.

    This is the current implemented bridge for the LLM Wiki operating intent:
    user keyword -> project/workstream search -> source-read evidence filter ->
    context pack. It does not write memory or enforce finish writeback gates.
    """

    identity = build_identity(repo_root.resolve())
    current_project_id = project_id or identity.project_id
    normalized_mode = "keyword" if mode == "auto" else mode if mode in {"keyword", "semantic", "hybrid"} else "hybrid"
    requested_backend = backend or config.search.backend
    result_backend = requested_backend if requested_backend in {"semantic_lite", "vector_local", "qmd"} else config.search.backend
    search_limit = max(limit * 2, limit)
    started_at = time.perf_counter()
    deadline = started_at + timeout_seconds if timeout_seconds is not None else None
    lookup_timing_ms: dict[str, int] = {}
    deadline_exceeded = False
    deadline_warnings: list[str] = []

    def remaining_seconds() -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.perf_counter())

    def config_for_remaining_time() -> SupervisorConfig:
        remaining = remaining_seconds()
        if remaining is None:
            return config
        scoped_config = config.model_copy(deep=True)
        scoped_config.search.qmd.timeout_seconds = max(1, min(scoped_config.search.qmd.timeout_seconds, math.ceil(remaining)))
        return scoped_config

    def empty_search_result(step: str, warning: str) -> MemorySearchResult:
        step_project_id = current_project_id if step.endswith("scoped") else None
        return MemorySearchResult(
            query=query,
            project_id=step_project_id,
            mode=normalized_mode if normalized_mode in {"keyword", "semantic", "hybrid"} else "hybrid",
            backend=result_backend,
            warnings=[warning],
            hits=[],
        )

    def run_search_step(
        step: str,
        *,
        step_project_id: str | None,
        step_mode: str,
    ) -> MemorySearchResult:
        nonlocal deadline_exceeded
        remaining = remaining_seconds()
        if remaining is not None and remaining <= 0:
            deadline_exceeded = True
            warning = f"MEMORY_LOOKUP_DEADLINE_EXCEEDED: skipped {step}."
            deadline_warnings.append(warning)
            return empty_search_result(step, warning)

        step_started = time.perf_counter()
        result = memory_search(
            query,
            project_id=step_project_id,
            limit=search_limit,
            mode=step_mode,
            backend=requested_backend,
            config=config_for_remaining_time(),
        )
        lookup_timing_ms[step] = int(round((time.perf_counter() - step_started) * 1000))
        remaining = remaining_seconds()
        if remaining is not None and remaining <= 0:
            deadline_exceeded = True
            warning = f"MEMORY_LOOKUP_DEADLINE_EXCEEDED_AFTER_STEP: {step} consumed the lookup budget."
            deadline_warnings.append(warning)
        return result

    scoped = run_search_step("primary_scoped", step_project_id=current_project_id, step_mode=normalized_mode)
    global_result = run_search_step("primary_global", step_project_id=None, step_mode=normalized_mode)
    global_hits, global_warnings = _enrich_search_hits_with_evidence(global_result.hits, current_project_id=current_project_id)
    extra_hits: list[MemorySearchHit] = []
    extra_warnings: list[str] = []
    if normalized_mode == "hybrid":
        keyword_scoped = run_search_step("keyword_scoped", step_project_id=current_project_id, step_mode="keyword")
        keyword_global = run_search_step("keyword_global", step_project_id=None, step_mode="keyword")
        keyword_global_hits, keyword_global_warnings = _enrich_search_hits_with_evidence(keyword_global.hits, current_project_id=current_project_id)
        extra_hits.extend([*keyword_scoped.hits, *keyword_global_hits])
        extra_warnings.extend([*keyword_scoped.warnings, *keyword_global.warnings, *keyword_global_warnings])

    combined = _dedupe_hits([*scoped.hits, *global_hits, *extra_hits])

    allowed_hits = [hit for hit in combined if hit.evidence_allowed][:limit]
    rejected_hits = [hit for hit in combined if not hit.evidence_allowed]
    candidates = _infer_workstream_candidates(query, allowed_hits, requested_workstream_id=workstream_id)
    inferred_workstream_id = candidates[0].workstream_id if candidates else None

    lookup_timing_ms["total"] = int(round((time.perf_counter() - started_at) * 1000))
    search_warnings = _unique_warnings([*scoped.warnings, *global_result.warnings, *global_warnings, *extra_warnings, *deadline_warnings])
    warnings = list(search_warnings)
    if rejected_hits:
        warnings.append(f"MEMORY_LOOKUP_REFERENCE_ONLY_HITS: {len(rejected_hits)} hit(s) were excluded from evidence.")
    if not allowed_hits:
        warnings.append("MEMORY_LOOKUP_NO_ALLOWED_EVIDENCE: no scoped/global source-read evidence matched the query.")
    if inferred_workstream_id is None:
        warnings.append("MEMORY_LOOKUP_WORKSTREAM_UNKNOWN: no workstream could be inferred from query or evidence.")

    source_paths = [hit.path for hit in allowed_hits]
    context_pack = MemoryContextPack(
        query=query,
        project_id=current_project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        workstream_id=inferred_workstream_id,
        source_paths=source_paths,
        sources=allowed_hits,
        rejected_reference_paths=[hit.path for hit in rejected_hits[:limit]],
        summary=_context_pack_summary(allowed_hits),
        next_actions=[
            "Read the returned source_paths before editing if more detail is needed.",
            "Use this context pack as memory evidence in harness_plan for non-trivial work.",
            "Do not cite rejected_reference_paths as evidence unless they are reclassified scope: global or current project.",
        ],
        warnings=warnings,
    )
    return MemoryLookupResult(
        query=query,
        project_id=current_project_id,
        workspace_id=identity.workspace_id,
        repo_root=identity.repo_root,
        mode=normalized_mode,
        backend=scoped.backend,
        requested_workstream_id=workstream_id,
        inferred_workstream_id=inferred_workstream_id,
        workstream_candidates=candidates,
        search_warnings=search_warnings,
        warnings=warnings,
        lookup_timing_ms=lookup_timing_ms,
        lookup_timeout_seconds=timeout_seconds,
        lookup_deadline_exceeded=deadline_exceeded,
        context_pack=context_pack,
    )
