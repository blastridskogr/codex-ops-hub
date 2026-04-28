"""Optional QMD search backend bridge."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import normalize_windows_path
from codex_hermes_supervisor.schemas.project_memory import MemorySearchHit


class QmdDoctorReport(BaseModel):
    backend_requested: str
    executable: str
    executable_found: bool
    version: str | None = None
    collection_roots: list[str] = Field(default_factory=list)
    collection_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QmdSyncReport(BaseModel):
    executable: str
    synced_collections: list[dict[str, str]] = Field(default_factory=list)
    refreshed_collections: list[str] = Field(default_factory=list)
    embed_requested: bool = False
    embed_completed: bool = False
    warnings: list[str] = Field(default_factory=list)


_NAME_SANITIZER = re.compile(r"[^a-z0-9_-]+")


def _command_version(executable: str, *, timeout_seconds: int, command_prefix: list[str] | None = None) -> str | None:
    try:
        completed = subprocess.run(
            [executable, *(command_prefix or []), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return (completed.stdout or completed.stderr).strip() or None


def build_qmd_doctor_report(config: SupervisorConfig) -> QmdDoctorReport:
    executable = config.search.qmd.executable
    found = shutil.which(executable) is not None or Path(executable).exists()
    roots = [normalize_windows_path(root) for root in config.qmd_collection_roots]
    names = [_collection_name(root) for root in config.qmd_collection_roots]
    warnings: list[str] = []
    if not roots:
        warnings.append("No QMD collection roots configured.")
    if not found:
        warnings.append("QMD executable not found; backend will fall back to semantic-lite.")
    return QmdDoctorReport(
        backend_requested=config.search.backend,
        executable=executable,
        executable_found=found,
        version=_command_version(executable, timeout_seconds=config.search.qmd.timeout_seconds, command_prefix=config.search.qmd.command_prefix) if found else None,
        collection_roots=roots,
        collection_names=names,
        warnings=warnings,
    )


def _collection_name(path: Path) -> str:
    slug = _NAME_SANITIZER.sub("-", path.name.lower()).strip("-")
    return slug or "codexwiki"


def _run_qmd_command(config: SupervisorConfig, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [config.search.qmd.executable, *config.search.qmd.command_prefix, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=config.search.qmd.timeout_seconds,
        check=True,
    )


def _parse_qmd_json_output(stdout: str) -> object:
    """Parse QMD JSON even when query/vsearch appends progress logs."""

    stripped = stdout.lstrip()
    payload, _ = json.JSONDecoder().raw_decode(stripped)
    return payload


def sync_qmd_collections(config: SupervisorConfig, *, embed: bool = False, force_embed: bool = False) -> QmdSyncReport:
    doctor = build_qmd_doctor_report(config)
    if not doctor.executable_found:
        return QmdSyncReport(
            executable=config.search.qmd.executable,
            embed_requested=embed,
            warnings=doctor.warnings,
        )
    if not config.qmd_collection_roots:
        return QmdSyncReport(
            executable=config.search.qmd.executable,
            embed_requested=embed,
            warnings=["No QMD collection roots configured."],
        )

    synced: list[dict[str, str]] = []
    refreshed: list[str] = []
    warnings: list[str] = []
    for root in config.qmd_collection_roots:
        name = _collection_name(root)
        try:
            _run_qmd_command(config, ["collection", "add", str(root), "--name", name])
        except subprocess.CalledProcessError as exc:
            details = (exc.stdout or exc.stderr or "").strip()
            # Treat duplicate collection registration as non-fatal.
            if "already exists" not in details.lower():
                raise
            warnings.append(f"Collection {name} already exists.")
        synced.append({"name": name, "path": normalize_windows_path(root)})
        _run_qmd_command(config, ["update", "-c", name])
        refreshed.append(name)

    embed_completed = False
    if embed:
        args = ["embed"]
        if force_embed:
            args.append("-f")
        _run_qmd_command(config, args)
        embed_completed = True

    return QmdSyncReport(
        executable=config.search.qmd.executable,
        synced_collections=synced,
        refreshed_collections=refreshed,
        embed_requested=embed,
        embed_completed=embed_completed,
        warnings=warnings,
    )


def _resolve_qmd_path(raw_path: str, collection_map: dict[str, Path]) -> str:
    resolved = _resolve_qmd_path_object(raw_path, collection_map)
    if resolved is not None:
        return normalize_windows_path(resolved)
    return normalize_windows_path(Path(str(raw_path)))


def _find_qmd_child(parent: Path, name: str) -> Path | None:
    direct = parent / name
    if direct.exists():
        return direct
    normalized = name.lower()
    candidates = {normalized, f"_{normalized}"}
    for child in parent.iterdir():
        child_name = child.name.lower()
        if child_name in candidates or child_name.lstrip("_") == normalized:
            return child
    return None


def _resolve_qmd_relative_path(root: Path, rel: str) -> Path | None:
    current = root
    parts = [part for part in rel.replace("\\", "/").split("/") if part]
    for part in parts:
        child = _find_qmd_child(current, part)
        if child is None:
            return None
        current = child
    return current.resolve()


def _resolve_qmd_candidate_path(candidate: Path) -> Path:
    """Resolve QMD paths while tolerating stripped trailing hyphens in links."""

    if candidate.exists():
        return candidate.resolve()
    if candidate.suffix:
        trailing_hyphen = candidate.with_name(f"{candidate.stem}-{candidate.suffix}")
        if trailing_hyphen.exists():
            return trailing_hyphen.resolve()
    return candidate.resolve()


def _resolve_qmd_path_object(raw_path: str, collection_map: dict[str, Path]) -> Path | None:
    try:
        if raw_path.startswith("qmd://"):
            remainder = raw_path[len("qmd://") :]
            collection, _, rel = remainder.partition("/")
            root = collection_map.get(collection)
            if root is None:
                return None
            resolved = _resolve_qmd_relative_path(root, rel)
            if resolved is not None:
                return resolved
            return _resolve_qmd_candidate_path(root / rel.replace("/", "\\"))
        return _resolve_qmd_candidate_path(Path(str(raw_path)))
    except OSError:
        return None


def _is_under_allowed_roots(path: Path, allowed_roots: list[Path]) -> bool:
    normalized = str(path.resolve()).lower().rstrip("\\/")
    for root in allowed_roots:
        candidate = str(root.resolve()).lower().rstrip("\\/")
        if normalized == candidate or normalized.startswith(candidate + "\\") or normalized.startswith(candidate + "/"):
            return True
    return False


def _normalize_hits(
    payload: object,
    *,
    project_id: str | None,
    collection_map: dict[str, Path],
    allowed_roots: list[Path],
    reject_out_of_scope: bool,
) -> tuple[list[MemorySearchHit], int]:
    if isinstance(payload, dict):
        items = payload.get("hits") or payload.get("results") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    hits: list[MemorySearchHit] = []
    dropped_out_of_scope = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path") or item.get("file") or item.get("source")
        if not raw_path:
            continue
        resolved_path = _resolve_qmd_path_object(str(raw_path), collection_map)
        if resolved_path is None:
            dropped_out_of_scope += 1
            continue
        if reject_out_of_scope and not _is_under_allowed_roots(resolved_path, allowed_roots):
            dropped_out_of_scope += 1
            continue
        raw_kind = str(item.get("kind") or "note")
        kind = raw_kind if raw_kind in {"manifest", "note", "outbox"} else "note"
        score = _normalize_qmd_score(item.get("score", 0))
        hits.append(
            MemorySearchHit(
                kind=kind,
                path=normalize_windows_path(resolved_path),
                project_id=item.get("project_id") or project_id,
                score=score,
                snippet=str(item.get("snippet") or item.get("summary") or ""),
            )
        )
    return hits, dropped_out_of_scope


def _normalize_qmd_score(raw_score: object) -> int:
    try:
        numeric = float(raw_score)
    except (TypeError, ValueError):
        return 0
    if -1.0 <= numeric <= 1.0:
        return int(round(numeric * 1000))
    return int(round(numeric))


def qmd_search(
    config: SupervisorConfig,
    *,
    query: str,
    project_id: str | None,
    limit: int,
    mode: str,
) -> tuple[list[MemorySearchHit], list[str]]:
    doctor = build_qmd_doctor_report(config)
    if not doctor.executable_found:
        return [], doctor.warnings
    if not doctor.collection_roots:
        return [], doctor.warnings

    def run_search(search_mode: str) -> tuple[list[MemorySearchHit], list[str]]:
        subcommand = {"keyword": "search", "semantic": "vsearch", "hybrid": "query"}.get(search_mode, "search")
        args = [
            subcommand,
            query,
            "--json",
            "-n",
            str(limit),
        ]
        for collection_name in doctor.collection_names:
            args.extend(["-c", collection_name])

        try:
            completed = _run_qmd_command(config, args)
        except FileNotFoundError:
            return [], ["QMD executable not found at runtime; falling back to semantic-lite."]
        except subprocess.TimeoutExpired:
            return [], ["QMD search timed out; falling back to semantic-lite."]
        except subprocess.CalledProcessError as exc:
            details = (exc.stdout or exc.stderr or "").strip()
            warning = "QMD search command failed; falling back to semantic-lite."
            if details:
                warning = f"{warning} {details}"
            return [], [warning]

        stdout = (completed.stdout or "").strip()
        if not stdout:
            return [], ["QMD returned no output; falling back to semantic-lite."]
        try:
            payload = _parse_qmd_json_output(stdout)
        except json.JSONDecodeError:
            return [], ["QMD returned invalid JSON; falling back to semantic-lite."]

        collection_map = {name: root for name, root in zip(doctor.collection_names, config.qmd_collection_roots, strict=False)}
        hits, dropped_out_of_scope = _normalize_hits(
            payload,
            project_id=project_id,
            collection_map=collection_map,
            allowed_roots=config.qmd_collection_roots,
            reject_out_of_scope=config.search.qmd.reject_hits_outside_collection_roots,
        )
        warnings: list[str] = []
        if dropped_out_of_scope:
            warnings.append(
                f"QMD_HITS_DROPPED_OUT_OF_SCOPE: {dropped_out_of_scope} hit(s) were dropped because they were outside configured collection roots."
            )
        return hits, warnings

    hits, warnings = run_search(mode)
    if hits or warnings or mode == "keyword":
        return hits, warnings

    keyword_hits, keyword_warnings = run_search("keyword")
    if keyword_hits:
        keyword_warnings.append("QMD_SEMANTIC_EMPTY_USED_KEYWORD_FALLBACK")
        return keyword_hits, keyword_warnings
    return hits, warnings
