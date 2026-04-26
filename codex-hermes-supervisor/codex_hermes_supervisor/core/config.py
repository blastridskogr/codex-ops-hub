"""Supervisor configuration and discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from yaml import YAMLError
from pydantic import BaseModel, Field

from .atomic_write import atomic_write_text
from . import paths
from .identity import normalize_windows_path


class CodexConfig(BaseModel):
    skill_root: str | None = None
    skill_root_discovery: list[str] = Field(
        default_factory=lambda: [
            "%USERPROFILE%/.codex/skills",
            "%USERPROFILE%/.agents/skills",
        ]
    )
    create_skill_root_if_missing: bool = True


class StateConfig(BaseModel):
    root: str = "%USERPROFILE%/.codex-hermes/state"
    lock_timeout_seconds: int = 30
    heartbeat_seconds: int = 5


class HermesConfig(BaseModel):
    profile: str = "coder"
    mode: Literal["outbox", "builtin_file", "cli", "python_library"] = "outbox"
    fail_open: bool = True
    outbox_dir: str = "%USERPROFILE%/.codex-hermes/hermes_outbox"
    read_builtin_files: bool = False
    cli_executable: str = "py"
    cli_write_args: list[str] = Field(default_factory=lambda: ["-m", "codex_hermes_supervisor.integrations.hermes_runtime"])
    cli_read_args: list[str] = Field(default_factory=lambda: ["-m", "codex_hermes_supervisor.integrations.hermes_runtime"])
    python_module: str = "codex_hermes_supervisor.integrations.hermes_runtime"
    python_write_symbol: str = "append_memory"
    python_read_symbol: str = "build_recall"
    builtin_recall_max_total_chars: int = 6000
    builtin_recall_overflow_policy: Literal["head", "tail", "head_tail"] = "head_tail"
    builtin_recall_include_truncation_metadata: bool = True
    builtin_recall_memory_chars: int = 3000
    builtin_recall_user_chars: int = 2000
    builtin_recall_soul_chars: int = 1000


class ObsidianConfig(BaseModel):
    enabled: bool = False
    vault_root: str = ""
    wiki_root: str = "CodexWiki"
    link_style: Literal["wikilink", "markdown"] = "wikilink"
    max_filename_stem_chars: int = 120
    max_full_path_chars: int = 240
    hash_suffix_chars: int = 8


class ManagedFilesConfig(BaseModel):
    default_policy: Literal["explicit_only"] = "explicit_only"
    allow_generated_detection: bool = False
    globs: list[str] = Field(default_factory=lambda: ["tools/generated/**", "reports/**", "artifacts/**"])
    never_manage_globs: list[str] = Field(
        default_factory=lambda: [
            "src/**",
            "app/**",
            "pages/**",
            "components/**",
            "package.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "package-lock.json",
            "migrations/**",
        ]
    )


class GitGuardConfig(BaseModel):
    include_untracked: bool = True
    include_staged: bool = True
    forbidden_patterns: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "**/secrets/**",
            "**/*secret*",
            "**/*token*",
            "**/node_modules/**",
            "**/dist/**",
            "**/build/**",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        ]
        )


class StrictModeConfig(BaseModel):
    enabled: bool = False
    write_policy: Literal["finish_gate", "supervisor_only"] = "finish_gate"
    allow_direct_codex_edits: bool = True
    allow_supervisor_apply_patch: bool = False
    allow_supervisor_managed_file_write: bool = False


class QmdConfig(BaseModel):
    enabled: bool = False
    executable: str = "qmd"
    command_prefix: list[str] = Field(default_factory=list)
    timeout_seconds: int = 20
    collection_roots: list[str] = Field(default_factory=list)
    reject_hits_outside_collection_roots: bool = True
    fallback_to_vector_local_if_all_hits_filtered: bool = True


class VectorLocalConfig(BaseModel):
    enabled: bool = True
    dimensions: int = 256
    auto_reindex: bool = True


class SearchConfig(BaseModel):
    backend: Literal["semantic_lite", "vector_local", "qmd"] = "semantic_lite"
    vector_local: VectorLocalConfig = Field(default_factory=VectorLocalConfig)
    qmd: QmdConfig = Field(default_factory=QmdConfig)


class MemoryPolicyConfig(BaseModel):
    store_raw_logs: bool = False
    store_raw_diffs: bool = False
    store_secrets: bool = False
    max_hermes_summary_chars: int = 1200


class TaskRecordsConfig(BaseModel):
    mode: Literal["global", "repo"] = "global"
    expose_summary_via_mcp: bool = True


class SupervisorConfig(BaseModel):
    config_schema_version: int = 1
    codex: CodexConfig = Field(default_factory=CodexConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    hermes: HermesConfig = Field(default_factory=HermesConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    managed_files: ManagedFilesConfig = Field(default_factory=ManagedFilesConfig)
    git_guard: GitGuardConfig = Field(default_factory=GitGuardConfig)
    strict_mode: StrictModeConfig = Field(default_factory=StrictModeConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    memory_policy: MemoryPolicyConfig = Field(default_factory=MemoryPolicyConfig)
    task_records: TaskRecordsConfig = Field(default_factory=TaskRecordsConfig)

    def expand(self, value: str) -> Path:
        return Path(value.replace("%USERPROFILE%", str(paths.user_home()))).resolve()

    @property
    def state_root(self) -> Path:
        return self.expand(self.state.root)

    @property
    def hermes_outbox_root(self) -> Path:
        return self.expand(self.hermes.outbox_dir)

    @property
    def obsidian_root(self) -> Path | None:
        if not self.obsidian.vault_root:
            return None
        return Path(self.obsidian.vault_root).expanduser().resolve()

    @property
    def qmd_collection_roots(self) -> list[Path]:
        roots: list[Path] = []
        for root in self.search.qmd.collection_roots:
            if isinstance(root, Path):
                candidate = root
            else:
                candidate = Path(str(root).replace("%USERPROFILE%", str(paths.user_home())))
            roots.append(candidate.expanduser().resolve())
        if roots:
            return roots
        if self.obsidian_root is not None:
            return [self.obsidian_root / self.obsidian.wiki_root]
        return []

    @property
    def cache_root(self) -> Path:
        return paths.supervisor_cache_root()

    def discover_skill_root(self, *, user_home: Path | None = None) -> tuple[Path, list[dict[str, str]]]:
        candidates: list[Path] = []
        status_rows: list[dict[str, str]] = []
        home = user_home or paths.user_home()

        if self.codex.skill_root:
            chosen = Path(self.codex.skill_root).expanduser().resolve()
            return chosen, [{"path": normalize_windows_path(chosen), "status": "explicit"}]

        for raw in self.codex.skill_root_discovery:
            candidate = Path(raw.replace("%USERPROFILE%", str(home))).resolve()
            candidates.append(candidate)
            status_rows.append(
                {
                    "path": normalize_windows_path(candidate),
                    "status": "found" if candidate.exists() else "missing",
                }
            )
            if candidate.exists():
                return candidate, status_rows

        fallback = (home / ".agents" / "skills").resolve()
        status_rows.append({"path": normalize_windows_path(fallback), "status": "would_create"})
        return fallback, status_rows


def load_config(config_path: Path | None = None) -> SupervisorConfig:
    path = config_path or paths.supervisor_config_path()
    if not path.exists():
        return SupervisorConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, YAMLError):
        return SupervisorConfig()
    return SupervisorConfig.model_validate(data)


def save_config(config: SupervisorConfig, config_path: Path | None = None) -> Path:
    path = config_path or paths.supervisor_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="python")
    atomic_write_text(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=False))
    return path


def config_to_dict(config: SupervisorConfig) -> dict[str, object]:
    return config.model_dump(mode="python")


def update_config_value(config: SupervisorConfig, key_path: str, value: object) -> SupervisorConfig:
    payload = config_to_dict(config)
    cursor: dict[str, object] = payload
    parts = key_path.split(".")
    for part in parts[:-1]:
        next_cursor = cursor.get(part)
        if not isinstance(next_cursor, dict):
            next_cursor = {}
            cursor[part] = next_cursor
        cursor = next_cursor
    cursor[parts[-1]] = value
    return SupervisorConfig.model_validate(payload)
