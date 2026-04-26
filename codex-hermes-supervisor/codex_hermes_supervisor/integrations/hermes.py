"""Hermes outbox, recall, and direct built-in memory helpers."""

from __future__ import annotations

import importlib
import hashlib
import importlib.util
import inspect
import json
import sys
import subprocess
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.core.paths import user_home
from codex_hermes_supervisor.schemas.tools import HermesRecallFilter, HermesRecallSource


class HermesRuntimeReport(BaseModel):
    requested_mode: str
    effective_mode: str
    read_effective_mode: str
    runtime_source: str
    read_runtime_source: str
    cli_executable: str
    cli_available: bool
    python_module: str
    python_module_available: bool
    cli_write_ready: bool = False
    cli_read_ready: bool = False
    python_write_ready: bool = False
    python_read_ready: bool = False
    builtin_memory_path: str
    warnings: list[str] = Field(default_factory=list)


class HermesWriteResult(BaseModel):
    requested_mode: str
    actual_mode: str
    path: str
    fallback: bool = False
    fallback_reason: str | None = None


class HermesRecallBundle(BaseModel):
    requested_mode: str
    actual_mode: str
    fallback: bool = False
    fallback_reason: str | None = None
    recall: str = ""
    sources: list[HermesRecallSource] = Field(default_factory=list)
    recall_filter: HermesRecallFilter | None = None


class HermesSelfTestReport(BaseModel):
    requested_mode: str
    runtime_source: str
    read_runtime_source: str
    write_ok: bool
    read_ok: bool
    actual_write_mode: str
    actual_read_mode: str
    write_path: str | None = None
    write_probe_performed: bool = False
    probe_cleanup_ok: bool | None = None
    fallback_on_write: bool
    fallback_on_read: bool
    token: str | None = None
    recall_contains_token: bool
    source_types: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HermesDirectModeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _HermesRuntimeState:
    requested_mode: str
    write_mode: str
    read_mode: str
    cli_available: bool
    python_module_available: bool
    cli_write_ready: bool
    cli_read_ready: bool
    python_write_ready: bool
    python_read_ready: bool
    warnings: list[str]


_VALID_OUTBOX_SCOPES = {"global", "project", "workspace", "user"}


def _stable_note_hash(
    *,
    note_type: str,
    task_id: str,
    project_id: str,
    workspace_id: str,
    title: str,
    compact_summary: str,
    pointer: str | None,
) -> str:
    raw = "\n".join(
        [
            note_type,
            task_id,
            project_id,
            workspace_id,
            title.strip(),
            compact_summary.strip(),
            (pointer or "").strip(),
        ]
    )
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _content_hash(body: str) -> str:
    return f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"


def _memory_kind_for_note_type(note_type: str) -> str:
    if note_type == "handoff":
        return "handoff"
    if note_type == "project_baseline":
        return "project_fact"
    if note_type == "lesson":
        return "lesson"
    return "project_fact"


def _bucket_name_for_note_type(note_type: str) -> str:
    if note_type == "handoff":
        return "handoff"
    if note_type == "project_baseline":
        return "project-baseline"
    return "lessons"


def _partition_root_for_scope(
    outbox_root: Path,
    *,
    scope: str,
    project_id: str,
    workspace_id: str,
) -> Path:
    if scope in {"global", "user"}:
        return outbox_root / "global"
    if scope == "workspace":
        if not workspace_id:
            raise ValueError("workspace-scoped Hermes outbox notes require workspace_id")
        return outbox_root / "workspaces" / workspace_id
    if scope == "project":
        if not project_id:
            raise ValueError("project-scoped Hermes outbox notes require project_id")
        return outbox_root / "projects" / project_id
    raise ValueError(f"Invalid Hermes outbox scope: {scope}")


def _iter_outbox_bucket_files(outbox_root: Path, bucket_name: str) -> list[Path]:
    files: list[Path] = []
    legacy_bucket = outbox_root / bucket_name
    if legacy_bucket.exists():
        files.extend(path for path in legacy_bucket.glob("*.md") if path.is_file())
    for partition_name in ("global", "projects", "workspaces", "legacy"):
        partition = outbox_root / partition_name
        if partition.exists():
            files.extend(path for path in partition.rglob("*.md") if path.parent.name == bucket_name)
    return sorted(set(files), key=lambda item: item.stat().st_mtime, reverse=True)


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    text = str(value)
    if not text:
        return '""'
    if all(ch.isalnum() or ch in {"_", "-", ".", "/"} for ch in text):
        return text
    return json.dumps(text)


def _frontmatter_line(key: str, value: object) -> str:
    return f"{key}: {_yaml_scalar(value)}\n"


def _parse_frontmatter(path: Path) -> dict[str, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        return {}
    metadata: dict[str, str | None] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if value in {"", "null", "Null", "NULL", "~"}:
            metadata[key.strip()] = None
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            try:
                decoded = json.loads(value) if value.startswith('"') else value[1:-1]
            except json.JSONDecodeError:
                decoded = value.strip("\"'")
            metadata[key.strip()] = str(decoded)
        else:
            metadata[key.strip()] = value
    return metadata


def write_outbox_note(
    outbox_root: Path,
    *,
    note_type: str,
    task_id: str,
    project_id: str,
    workspace_id: str,
    title: str,
    compact_summary: str,
    repo_root: str | None = None,
    scope: str = "project",
    memory_kind: str | None = None,
    source_tool: str = "manual",
    pointer: str | None = None,
) -> tuple[Path, str]:
    """Write a deduplicated Hermes outbox note."""

    if scope not in _VALID_OUTBOX_SCOPES:
        raise ValueError(f"Invalid Hermes outbox scope: {scope}")
    memory_kind = memory_kind or _memory_kind_for_note_type(note_type)
    bucket_name = _bucket_name_for_note_type(note_type)
    bucket = _partition_root_for_scope(outbox_root, scope=scope, project_id=project_id, workspace_id=workspace_id) / bucket_name
    bucket.mkdir(parents=True, exist_ok=True)
    stable_hash = _stable_note_hash(
        note_type=note_type,
        task_id=task_id,
        project_id=project_id,
        workspace_id=workspace_id,
        title=title,
        compact_summary=compact_summary,
        pointer=pointer,
    )
    body = "---\n"
    body += _frontmatter_line("outbox_schema_version", 2)
    body += _frontmatter_line("type", note_type)
    body += _frontmatter_line("created", now_local_iso())
    body += _frontmatter_line("scope", scope)
    body += _frontmatter_line("project_id", project_id if scope in {"project", "workspace"} else project_id or None)
    body += _frontmatter_line("workspace_id", workspace_id if scope == "workspace" else workspace_id or None)
    body += _frontmatter_line("repo_root", repo_root)
    body += _frontmatter_line("memory_kind", memory_kind)
    body += _frontmatter_line("source_task_id", task_id)
    body += _frontmatter_line("source_tool", source_tool)
    body += _frontmatter_line("task_id", task_id)
    body += _frontmatter_line("target", "hermes")
    body += _frontmatter_line("status", "pending_import")
    body += _frontmatter_line("content_hash", stable_hash)
    body += "---\n\n"
    body += f"# {title}\n\n{compact_summary}\n"
    if pointer:
        body += f"\n{pointer}\n"
    for existing in bucket.glob("*.md"):
        if f"content_hash: {stable_hash}" in existing.read_text(encoding="utf-8"):
            return existing, stable_hash
    filename = f"{task_id}.md"
    path = bucket / filename
    atomic_write_text(path, body)
    return path, stable_hash


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


def _extract_note_summary(path: Path) -> str:
    text = _strip_frontmatter(path.read_text(encoding="utf-8"))
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if line.startswith("# "):
            continue
        if not line.strip():
            continue
        cleaned.append(line.strip())
    return " ".join(cleaned).strip()


def _filter_outbox_note(
    path: Path,
    *,
    recall_filter: HermesRecallFilter,
    filtering_enabled: bool,
) -> tuple[bool, dict[str, str | None]]:
    metadata = _parse_frontmatter(path)
    if not filtering_enabled:
        recall_filter.allowed_count += 1
        return True, metadata

    scope = metadata.get("scope")
    if not scope:
        recall_filter.legacy_unscoped_count += 1
        return False, metadata
    if scope not in _VALID_OUTBOX_SCOPES:
        recall_filter.rejected_invalid_scope_count += 1
        return False, metadata
    if scope in {"global", "user"}:
        recall_filter.allowed_count += 1
        return True, metadata
    if scope == "project":
        project_id = metadata.get("project_id")
        if not project_id or not recall_filter.current_project_id:
            recall_filter.rejected_missing_identity_count += 1
            return False, metadata
        if project_id == recall_filter.current_project_id:
            recall_filter.allowed_count += 1
            return True, metadata
        recall_filter.filtered_cross_project_count += 1
        return False, metadata
    if scope == "workspace":
        workspace_id = metadata.get("workspace_id")
        if not workspace_id or not recall_filter.current_workspace_id:
            recall_filter.rejected_missing_identity_count += 1
            return False, metadata
        if workspace_id == recall_filter.current_workspace_id:
            recall_filter.allowed_count += 1
            return True, metadata
        recall_filter.filtered_cross_project_count += 1
        return False, metadata
    recall_filter.rejected_invalid_scope_count += 1
    return False, metadata


def _latest_outbox_summaries(
    outbox_root: Path,
    bucket_name: str,
    *,
    max_items: int,
    recall_filter: HermesRecallFilter,
    filtering_enabled: bool,
) -> tuple[list[str], list[dict[str, str | None]]]:
    summaries: list[str] = []
    metadata_items: list[dict[str, str | None]] = []
    for path in _iter_outbox_bucket_files(outbox_root, bucket_name):
        if len(summaries) >= max_items:
            break
        allowed, metadata = _filter_outbox_note(path, recall_filter=recall_filter, filtering_enabled=filtering_enabled)
        if not allowed:
            continue
        summary = _extract_note_summary(path)
        if not summary:
            continue
        summaries.append(summary)
        metadata_items.append(metadata)
    return summaries, metadata_items


def _shared_source_metadata(items: list[dict[str, str | None]]) -> dict[str, str | None]:
    if not items:
        return {}
    keys = ["scope", "project_id", "workspace_id", "repo_root", "memory_kind"]
    shared: dict[str, str | None] = {}
    for key in keys:
        values = {item.get(key) for item in items}
        if len(values) == 1:
            shared[key] = values.pop()
    return shared


def _excerpt_text(text: str, max_chars: int, policy: str) -> tuple[str, bool]:
    marker = "\n\n...[truncated]...\n\n"
    if max_chars <= 0:
        return "", bool(text)
    if len(text) <= max_chars:
        return text, False
    if policy == "head":
        return text[:max_chars], True
    if policy == "tail":
        return text[-max_chars:], True
    if max_chars <= len(marker):
        return text[:max_chars], True
    remaining = max_chars - len(marker)
    head_len = remaining // 2
    tail_len = remaining - head_len
    return text[:head_len] + marker + text[-tail_len:], True


def _read_latest_lines(path: Path, *, max_lines: int = 5) -> list[str]:
    if not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[-max_lines:]


def _resolve_module_symbol(module_name: str, symbol_path: str) -> object | None:
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    current: object = module
    for part in symbol_path.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _is_bundled_python_runtime(config: SupervisorConfig) -> bool:
    return config.hermes.python_module == "codex_hermes_supervisor.integrations.hermes_runtime"


def _is_bundled_cli_runtime(config: SupervisorConfig) -> bool:
    return config.hermes.cli_executable == "py" and config.hermes.cli_write_args[:2] == ["-m", "codex_hermes_supervisor.integrations.hermes_runtime"]


def _runtime_source(config: SupervisorConfig, actual_mode: str) -> str:
    if actual_mode == "python_library":
        return "bundled_python" if _is_bundled_python_runtime(config) else "external_python"
    if actual_mode == "cli":
        return "bundled_cli" if _is_bundled_cli_runtime(config) else "external_cli"
    if actual_mode == "builtin_file":
        return "builtin_file"
    if actual_mode == "builtin_file_fallback":
        return "fallback_builtin_file"
    return actual_mode


def _invoke_python_adapter(callable_obj: object, payload: dict[str, object]) -> object:
    if not callable(callable_obj):
        raise HermesDirectModeError("HERMES_UNAVAILABLE", "Configured Hermes python symbol is not callable.")
    try:
        result = callable_obj(payload)
    except TypeError:
        result = callable_obj(**payload)
    if inspect.isawaitable(result):
        raise HermesDirectModeError("HERMES_DIRECT_MODE_NOT_IMPLEMENTED", "Async Hermes python adapters are not supported.")
    return result


def _render_cli_args(args: list[str], payload: dict[str, object]) -> list[str]:
    rendered: list[str] = []
    replacements = {key: str(value) for key, value in payload.items() if value is not None and not isinstance(value, (dict, list))}
    for arg in args:
        rendered.append(arg.format(**replacements))
    return rendered


def _invoke_cli_adapter(executable: str, args: list[str], payload: dict[str, object]) -> object:
    try:
        completed = subprocess.run(
            [executable, *_render_cli_args(args, payload)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except FileNotFoundError as exc:
        raise HermesDirectModeError("HERMES_UNAVAILABLE", f"Hermes CLI executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HermesDirectModeError("HERMES_UNAVAILABLE", "Hermes CLI adapter timed out.") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise HermesDirectModeError("HERMES_UNAVAILABLE", details or "Hermes CLI adapter failed.") from exc
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return stdout


def _resolve_runtime_state(config: SupervisorConfig) -> _HermesRuntimeState:
    cli_available = shutil.which(config.hermes.cli_executable) is not None or Path(config.hermes.cli_executable).exists()
    python_module_available = importlib.util.find_spec(config.hermes.python_module) is not None
    warnings: list[str] = []
    requested = config.hermes.mode
    write_mode = requested
    read_mode = requested
    cli_write_ready = cli_available and bool(config.hermes.cli_write_args)
    cli_read_ready = cli_available and bool(config.hermes.cli_read_args)
    python_write_ready = False
    python_read_ready = False
    if python_module_available:
        python_write_ready = _resolve_module_symbol(config.hermes.python_module, config.hermes.python_write_symbol) is not None
        python_read_ready = _resolve_module_symbol(config.hermes.python_module, config.hermes.python_read_symbol) is not None

    if requested == "builtin_file":
        write_mode = "builtin_file"
        read_mode = "builtin_file"
    elif requested == "cli":
        if not cli_available:
            write_mode = "builtin_file_fallback" if config.hermes.fail_open else "cli_unavailable"
            read_mode = "builtin_file_fallback" if config.hermes.fail_open else "cli_unavailable"
            warnings.append("Hermes CLI not found; using built-in file memory fallback.")
            if sys.platform.startswith("win") and not _is_bundled_cli_runtime(config):
                warnings.append("Upstream Hermes Agent native Windows CLI support is not available; use the bundled runtime or WSL.")
        else:
            if cli_write_ready:
                write_mode = "cli"
            else:
                write_mode = "builtin_file_fallback" if config.hermes.fail_open else "cli_not_implemented"
                warnings.append("Hermes CLI direct write command is not configured; using built-in file memory fallback.")
            if cli_read_ready:
                read_mode = "cli"
            else:
                read_mode = "builtin_file_fallback" if config.hermes.fail_open else "cli_not_implemented"
                warnings.append("Hermes CLI direct recall command is not configured; using built-in file memory fallback.")
    elif requested == "python_library":
        if not python_module_available:
            write_mode = "builtin_file_fallback" if config.hermes.fail_open else "python_library_unavailable"
            read_mode = "builtin_file_fallback" if config.hermes.fail_open else "python_library_unavailable"
            warnings.append("Hermes Python module not found; using built-in file memory fallback.")
            if sys.platform.startswith("win") and not _is_bundled_python_runtime(config):
                warnings.append("Upstream Hermes Agent native Windows Python runtime support is not available; use the bundled runtime or WSL.")
        else:
            if python_write_ready:
                write_mode = "python_library"
            else:
                write_mode = "builtin_file_fallback" if config.hermes.fail_open else "python_library_not_implemented"
                warnings.append("Hermes Python-library direct write symbol is not configured or missing; using built-in file memory fallback.")
            if python_read_ready:
                read_mode = "python_library"
            else:
                read_mode = "builtin_file_fallback" if config.hermes.fail_open else "python_library_not_implemented"
                warnings.append("Hermes Python-library direct recall symbol is not configured or missing; using built-in file memory fallback.")
    else:
        write_mode = requested
        read_mode = requested

    return _HermesRuntimeState(
        requested_mode=requested,
        write_mode=write_mode,
        read_mode=read_mode,
        cli_available=cli_available,
        python_module_available=python_module_available,
        cli_write_ready=cli_write_ready,
        cli_read_ready=cli_read_ready,
        python_write_ready=python_write_ready,
        python_read_ready=python_read_ready,
        warnings=list(dict.fromkeys(warnings)),
    )


def _normalize_write_result(output: object, *, requested_mode: str, actual_mode: str, fallback_reason: str | None) -> HermesWriteResult:
    if isinstance(output, str):
        path = output
    elif isinstance(output, dict):
        path = str(output.get("path") or output.get("target_path") or output.get("memory_path") or "")
        actual_mode = str(output.get("actual_mode") or actual_mode)
        fallback_reason = str(output.get("fallback_reason") or fallback_reason) if (output.get("fallback_reason") or fallback_reason) else None
    else:
        path = ""
    if not path:
        raise HermesDirectModeError("HERMES_UNAVAILABLE", "Hermes direct adapter returned no output path.")
    return HermesWriteResult(
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        path=path,
        fallback=actual_mode != requested_mode,
        fallback_reason=fallback_reason,
    )


def _normalize_recall_result(output: object, *, requested_mode: str, actual_mode: str, fallback_reason: str | None) -> HermesRecallBundle:
    if isinstance(output, str):
        recall = output.strip()
        sources = [
            HermesRecallSource(
                source_type=actual_mode,
                path=None,
                included_chars=len(recall),
                original_chars=len(recall),
                status="fallback" if actual_mode != requested_mode else "used",
            )
        ] if recall else []
    elif isinstance(output, dict):
        recall = str(output.get("recall") or output.get("text") or "").strip()
        actual_mode = str(output.get("actual_mode") or actual_mode)
        fallback_reason = str(output.get("fallback_reason") or fallback_reason) if (output.get("fallback_reason") or fallback_reason) else None
        raw_sources = output.get("sources") or []
        sources = [HermesRecallSource.model_validate(item) for item in raw_sources] if isinstance(raw_sources, list) else []
        if recall and not sources and output.get("path"):
            sources = [
                HermesRecallSource(
                    source_type=actual_mode,
                    path=str(output["path"]),
                    included_chars=len(recall),
                    original_chars=len(recall),
                    status="fallback" if actual_mode != requested_mode else "used",
                )
            ]
    else:
        recall = ""
        sources = []
    return HermesRecallBundle(
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        fallback=actual_mode != requested_mode,
        fallback_reason=fallback_reason,
        recall=recall,
        sources=sources,
    )


def _invoke_direct_write_adapter(config: SupervisorConfig, *, target: str, content: str, runtime: _HermesRuntimeState) -> HermesWriteResult:
    payload = {
        "operation": "write",
        "profile": config.hermes.profile,
        "target": target,
        "content": content,
        "requested_mode": runtime.requested_mode,
    }
    fallback_reason = runtime.warnings[0] if runtime.write_mode != runtime.requested_mode and runtime.warnings else None
    if runtime.write_mode == "cli":
        output = _invoke_cli_adapter(config.hermes.cli_executable, config.hermes.cli_write_args, payload)
        return _normalize_write_result(output, requested_mode=runtime.requested_mode, actual_mode="cli", fallback_reason=fallback_reason)
    if runtime.write_mode == "python_library":
        symbol = _resolve_module_symbol(config.hermes.python_module, config.hermes.python_write_symbol)
        output = _invoke_python_adapter(symbol, payload)
        return _normalize_write_result(output, requested_mode=runtime.requested_mode, actual_mode="python_library", fallback_reason=fallback_reason)
    raise HermesDirectModeError("HERMES_UNAVAILABLE", f"Hermes {runtime.requested_mode} direct write is not available.")


def _invoke_direct_read_adapter(config: SupervisorConfig, *, max_items: int, runtime: _HermesRuntimeState) -> HermesRecallBundle:
    payload = {
        "operation": "read",
        "profile": config.hermes.profile,
        "max_items": max_items,
        "requested_mode": runtime.requested_mode,
        "max_total_chars": config.hermes.builtin_recall_max_total_chars,
    }
    fallback_reason = runtime.warnings[0] if runtime.read_mode != runtime.requested_mode and runtime.warnings else None
    if runtime.read_mode == "cli":
        output = _invoke_cli_adapter(config.hermes.cli_executable, config.hermes.cli_read_args, payload)
        return _normalize_recall_result(output, requested_mode=runtime.requested_mode, actual_mode="cli", fallback_reason=fallback_reason)
    if runtime.read_mode == "python_library":
        symbol = _resolve_module_symbol(config.hermes.python_module, config.hermes.python_read_symbol)
        output = _invoke_python_adapter(symbol, payload)
        return _normalize_recall_result(output, requested_mode=runtime.requested_mode, actual_mode="python_library", fallback_reason=fallback_reason)
    raise HermesDirectModeError("HERMES_UNAVAILABLE", f"Hermes {runtime.requested_mode} direct recall is not available.")


def _hermes_builtin_candidates(profile: str) -> list[Path]:
    home = user_home()
    return [
        home / ".hermes" / "profiles" / profile / "memories" / "MEMORY.md",
        home / ".hermes" / "profiles" / profile / "memories" / "USER.md",
        home / ".hermes" / "profiles" / profile / "SOUL.md",
        home / ".hermes" / profile / "memories" / "MEMORY.md",
        home / ".hermes" / profile / "memories" / "USER.md",
        home / ".hermes" / profile / "SOUL.md",
        home / ".hermes" / "memories" / "MEMORY.md",
        home / ".hermes" / "memories" / "USER.md",
        home / ".hermes" / "SOUL.md",
    ]


def build_recall_bundle(
    config: SupervisorConfig,
    outbox_root: Path,
    tasks_dir: Path,
    *,
    max_items: int = 3,
    current_project_id: str | None = None,
    current_workspace_id: str | None = None,
) -> HermesRecallBundle:
    """Build compact recall text plus metadata about the actual recall sources."""

    runtime = _resolve_runtime_state(config)
    sources: list[HermesRecallSource] = []
    sections: list[str] = []
    recall_filter = HermesRecallFilter(
        current_project_id=current_project_id,
        current_workspace_id=current_workspace_id,
    )
    filtering_enabled = bool(current_project_id or current_workspace_id)

    latest_lessons = _read_latest_lines(tasks_dir / "lessons.md", max_lines=max_items)
    if latest_lessons:
        block = "Local lessons:\n- " + "\n- ".join(latest_lessons)
        sections.append(block)
        sources.append(
            HermesRecallSource(
                source_type="local_lessons",
                path=str(tasks_dir / "lessons.md"),
                included_chars=len(block),
                original_chars=len(block),
                status="used",
            )
        )

    handoff_summaries, handoff_metadata = _latest_outbox_summaries(
        outbox_root,
        "handoff",
        max_items=max_items,
        recall_filter=recall_filter,
        filtering_enabled=filtering_enabled,
    )
    if handoff_summaries:
        block = "Recent handoffs:\n- " + "\n- ".join(handoff_summaries)
        shared = _shared_source_metadata(handoff_metadata)
        sections.append(block)
        sources.append(
            HermesRecallSource(
                source_type="outbox",
                path=str(outbox_root / "handoff"),
                included_chars=len(block),
                original_chars=len(block),
                status="used",
                scope=shared.get("scope"),
                project_id=shared.get("project_id"),
                workspace_id=shared.get("workspace_id"),
                repo_root=shared.get("repo_root"),
                memory_kind=shared.get("memory_kind"),
            )
        )

    lesson_summaries, lesson_metadata = _latest_outbox_summaries(
        outbox_root,
        "lessons",
        max_items=max_items,
        recall_filter=recall_filter,
        filtering_enabled=filtering_enabled,
    )
    if lesson_summaries:
        block = "Recent Hermes lesson candidates:\n- " + "\n- ".join(lesson_summaries)
        shared = _shared_source_metadata(lesson_metadata)
        sections.append(block)
        sources.append(
            HermesRecallSource(
                source_type="outbox",
                path=str(outbox_root / "lessons"),
                included_chars=len(block),
                original_chars=len(block),
                status="used",
                scope=shared.get("scope"),
                project_id=shared.get("project_id"),
                workspace_id=shared.get("workspace_id"),
                repo_root=shared.get("repo_root"),
                memory_kind=shared.get("memory_kind"),
            )
        )

    direct_bundle: HermesRecallBundle | None = None
    if runtime.read_mode in {"cli", "python_library"}:
        direct_bundle = _invoke_direct_read_adapter(config, max_items=max_items, runtime=runtime)
        if direct_bundle.recall:
            sections.append(direct_bundle.recall)
            sources.extend(direct_bundle.sources)

    if direct_bundle is None and (config.hermes.read_builtin_files or runtime.read_mode in {"builtin_file", "builtin_file_fallback"}):
        remaining_budget = config.hermes.builtin_recall_max_total_chars - len("\n\n".join(sections))
        per_file_limits = {
            "MEMORY.md": config.hermes.builtin_recall_memory_chars,
            "USER.md": config.hermes.builtin_recall_user_chars,
            "SOUL.md": config.hermes.builtin_recall_soul_chars,
        }
        fallback = runtime.read_mode != runtime.requested_mode
        builtin_source_type = runtime.read_mode if runtime.read_mode in {"builtin_file", "builtin_file_fallback"} else runtime.requested_mode
        builtin_blocks: list[str] = []
        for candidate in _hermes_builtin_candidates(config.hermes.profile):
            if remaining_budget <= 0:
                break
            if not candidate.exists():
                continue
            text = candidate.read_text(encoding="utf-8").strip()
            if not text:
                continue
            file_limit = per_file_limits.get(candidate.name, config.hermes.builtin_recall_memory_chars)
            excerpt_limit = min(file_limit, remaining_budget)
            excerpt, truncated = _excerpt_text(text, excerpt_limit, config.hermes.builtin_recall_overflow_policy)
            block = f"{candidate.name}:\n{excerpt}"
            builtin_blocks.append(block)
            included = len(block)
            remaining_budget -= included + 2
            sources.append(
                HermesRecallSource(
                    source_type=builtin_source_type,
                    path=str(candidate),
                    included_chars=included,
                    original_chars=len(text),
                    truncated=truncated,
                    status="fallback" if fallback else "used",
                    overflow_policy=config.hermes.builtin_recall_overflow_policy if truncated else None,
                )
            )
            if len([source for source in sources if source.source_type.startswith("builtin") or source.source_type in {"cli", "python_library"}]) >= max_items:
                break
        if builtin_blocks:
            sections.append("Hermes builtin files:\n" + "\n\n".join(builtin_blocks))

    recall = "\n\n".join(section for section in sections if section).strip()
    if len(recall) > config.hermes.builtin_recall_max_total_chars:
        recall, _ = _excerpt_text(recall, config.hermes.builtin_recall_max_total_chars, config.hermes.builtin_recall_overflow_policy)

    if direct_bundle and recall and len(recall) > config.hermes.builtin_recall_max_total_chars:
        recall, truncated = _excerpt_text(recall, config.hermes.builtin_recall_max_total_chars, config.hermes.builtin_recall_overflow_policy)
        if truncated:
            for source in sources[:1]:
                source.truncated = True
                source.included_chars = len(recall)
                source.overflow_policy = config.hermes.builtin_recall_overflow_policy

    fallback_reason = runtime.warnings[0] if runtime.read_mode != runtime.requested_mode and runtime.warnings else None
    return HermesRecallBundle(
        requested_mode=runtime.requested_mode,
        actual_mode=runtime.read_mode,
        fallback=runtime.read_mode != runtime.requested_mode,
        fallback_reason=fallback_reason,
        recall=recall,
        sources=sources,
        recall_filter=recall_filter if filtering_enabled else None,
    )


def build_recall_context(
    outbox_root: Path,
    tasks_dir: Path,
    *,
    profile: str,
    read_builtin_files: bool,
    max_items: int = 3,
) -> str:
    config = SupervisorConfig.model_validate(
        {
            "hermes": {
                "profile": profile,
                "read_builtin_files": read_builtin_files,
                "mode": "builtin_file" if read_builtin_files else "outbox",
            }
        }
    )
    return build_recall_bundle(config, outbox_root, tasks_dir, max_items=max_items).recall


def build_runtime_report(config: SupervisorConfig) -> HermesRuntimeReport:
    runtime = _resolve_runtime_state(config)
    return HermesRuntimeReport(
        requested_mode=runtime.requested_mode,
        effective_mode=runtime.write_mode,
        read_effective_mode=runtime.read_mode,
        runtime_source=_runtime_source(config, runtime.write_mode),
        read_runtime_source=_runtime_source(config, runtime.read_mode),
        cli_executable=config.hermes.cli_executable,
        cli_available=runtime.cli_available,
        python_module=config.hermes.python_module,
        python_module_available=runtime.python_module_available,
        cli_write_ready=runtime.cli_write_ready,
        cli_read_ready=runtime.cli_read_ready,
        python_write_ready=runtime.python_write_ready,
        python_read_ready=runtime.python_read_ready,
        builtin_memory_path=str(hermes_memory_file(config.hermes.profile, "memory")),
        warnings=runtime.warnings,
    )


def _can_write_direct(runtime_report: HermesRuntimeReport) -> bool:
    if runtime_report.effective_mode == "builtin_file":
        return True
    if runtime_report.effective_mode == "cli":
        return runtime_report.cli_write_ready
    if runtime_report.effective_mode == "python_library":
        return runtime_report.python_write_ready
    return False


def _cleanup_probe_entry(path: Path, token: str) -> bool:
    if not path.exists():
        return False
    current = path.read_text(encoding="utf-8").strip()
    if not current:
        return False
    entries = [entry.strip() for entry in current.split("\n吏?n") if entry.strip()]
    remaining = [entry for entry in entries if token not in entry]
    if len(remaining) == len(entries):
        return False
    if remaining:
        atomic_write_text(path, "\n吏?n".join(remaining) + "\n")
    else:
        atomic_write_text(path, "")
    return True


def run_runtime_selftest(
    config: SupervisorConfig,
    *,
    backend: str | None = None,
    target: str = "memory",
    write_probe: bool = False,
) -> HermesSelfTestReport:
    if backend and backend != "configured":
        payload = config.model_dump(mode="python")
        hermes_payload = dict(payload["hermes"])
        hermes_payload["mode"] = backend
        payload["hermes"] = hermes_payload
        config = SupervisorConfig.model_validate(payload)

    runtime_report = build_runtime_report(config)
    token: str | None = None
    write_result: HermesWriteResult | None = None
    cleanup_ok: bool | None = None
    if write_probe:
        token = f"codex-hermes-selftest:{uuid.uuid4().hex}"
        write_result = append_direct_memory(config, token, target=target)
    with tempfile.TemporaryDirectory(prefix="codex-hermes-selftest-") as temp_root:
        temp_path = Path(temp_root)
        bundle = build_recall_bundle(config, temp_path / "outbox", temp_path / "tasks")
    if write_probe and write_result is not None:
        probe_path = Path(write_result.path)
        if probe_path.exists():
            cleanup_ok = _cleanup_probe_entry(probe_path, token or "")
        else:
            cleanup_ok = False
        if cleanup_ok is not True:
            raise HermesDirectModeError(
                "HERMES_SELFTEST_CLEANUP_FAILED",
                f"Selftest probe was written but could not be removed from {write_result.path}.",
            )
    warnings = list(runtime_report.warnings)
    if bundle.fallback_reason:
        warnings.append(bundle.fallback_reason)
    return HermesSelfTestReport(
        requested_mode=config.hermes.mode,
        runtime_source=runtime_report.runtime_source,
        read_runtime_source=runtime_report.read_runtime_source,
        write_ok=_can_write_direct(runtime_report),
        read_ok=True,
        actual_write_mode=write_result.actual_mode if write_result is not None else runtime_report.effective_mode,
        actual_read_mode=bundle.actual_mode,
        write_path=write_result.path if write_result is not None else None,
        write_probe_performed=write_probe,
        probe_cleanup_ok=cleanup_ok,
        fallback_on_write=write_result.fallback if write_result is not None else runtime_report.effective_mode != config.hermes.mode,
        fallback_on_read=bundle.fallback,
        token=token,
        recall_contains_token=bool(token and token in bundle.recall),
        source_types=[source.source_type for source in bundle.sources],
        warnings=list(dict.fromkeys(warnings)),
    )


def hermes_profile_root(profile: str) -> Path:
    home = user_home()
    candidate = home / ".hermes" / "profiles" / profile
    if candidate.exists():
        return candidate
    return home / ".hermes"


def hermes_memory_file(profile: str, target: str = "memory") -> Path:
    filename = "USER.md" if target == "user" else "MEMORY.md"
    root = hermes_profile_root(profile)
    return root / "memories" / filename


def append_builtin_memory(profile: str, content: str, *, target: str = "memory") -> Path:
    """Append a compact entry to Hermes built-in file memory."""

    memory_path = hermes_memory_file(profile, target)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    content = content.strip()
    if not content:
        return memory_path

    if memory_path.exists():
        current = memory_path.read_text(encoding="utf-8").strip()
    else:
        current = ""

    entries = [entry.strip() for entry in current.split("\n짠\n") if entry.strip()] if current else []
    if content in entries:
        return memory_path
    entries.append(content)
    atomic_write_text(memory_path, "\n짠\n".join(entries) + "\n")
    return memory_path


def append_direct_memory(config: SupervisorConfig, content: str, *, target: str = "memory") -> HermesWriteResult:
    runtime = _resolve_runtime_state(config)
    if runtime.requested_mode == "outbox":
        raise HermesDirectModeError(
            "HERMES_SYNC_REQUIRES_DIRECT_MODE",
            "Direct Hermes sync requires a non-outbox Hermes mode.",
        )
    if runtime.write_mode == "builtin_file":
        memory_path = append_builtin_memory(config.hermes.profile, content, target=target)
        return HermesWriteResult(
            requested_mode="builtin_file",
            actual_mode="builtin_file",
            path=str(memory_path),
            fallback=False,
            fallback_reason=None,
        )
    if runtime.write_mode in {"cli", "python_library"}:
        return _invoke_direct_write_adapter(config, target=target, content=content, runtime=runtime)
    if not config.hermes.fail_open and runtime.write_mode != runtime.requested_mode:
        if runtime.requested_mode == "cli" and not runtime.cli_available:
            raise HermesDirectModeError("HERMES_UNAVAILABLE", "Hermes CLI is not available.")
        if runtime.requested_mode == "python_library" and not runtime.python_module_available:
            raise HermesDirectModeError("HERMES_UNAVAILABLE", "Hermes Python module is not available.")
        raise HermesDirectModeError(
            "HERMES_DIRECT_MODE_NOT_IMPLEMENTED",
            f"Hermes {runtime.requested_mode} direct write is not implemented.",
        )
    memory_path = append_builtin_memory(config.hermes.profile, content, target=target)
    fallback = runtime.write_mode != runtime.requested_mode
    fallback_reason = runtime.warnings[0] if fallback and runtime.warnings else None
    return HermesWriteResult(
        requested_mode=runtime.requested_mode,
        actual_mode=runtime.write_mode,
        path=str(memory_path),
        fallback=fallback,
        fallback_reason=fallback_reason,
    )


def parse_outbox_compact_summary(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = _strip_frontmatter(text)
    lines = text.splitlines()
    capture = False
    collected: list[str] = []
    for line in lines:
        if line.startswith("# "):
            capture = True
            continue
        if not capture:
            continue
        collected.append(line)
    return "\n".join(collected).strip()


def import_outbox_note_to_memory(path: Path, *, profile: str, target: str = "memory") -> Path:
    summary = parse_outbox_compact_summary(path)
    imported = append_builtin_memory(profile, summary, target=target)
    return imported


def sync_outbox_to_direct_memory(
    config: SupervisorConfig,
    *,
    target: str = "memory",
    archive: bool = False,
) -> list[Path]:
    if config.hermes.mode == "outbox":
        raise HermesDirectModeError(
            "HERMES_SYNC_REQUIRES_DIRECT_MODE",
            "hermes-sync-outbox requires a non-outbox Hermes mode or an explicit builtin-file fallback target.",
        )
    imported: list[Path] = []
    for note in sorted(config.hermes_outbox_root.rglob("*.md")):
        if note.parent.name == "archived":
            continue
        text = note.read_text(encoding="utf-8")
        if "status: pending_import" not in text:
            continue
        summary = parse_outbox_compact_summary(note)
        memory_result = append_direct_memory(config, summary, target=target)
        atomic_write_text(note, text.replace("status: pending_import", "status: imported"))
        if archive:
            archive_dir = note.parent / "archived"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archived = archive_dir / note.name
            if archived.exists():
                archived.unlink()
            note.replace(archived)
        imported.append(Path(memory_result.path))
    return imported
