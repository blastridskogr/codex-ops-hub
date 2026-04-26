"""Dry-run installer preview."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core import paths
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import normalize_windows_path
from codex_hermes_supervisor.services.agent_compat import validate_generated_agent_templates
from codex_hermes_supervisor.services.install_global import get_managed_agent_templates
from codex_hermes_supervisor.services.install_project import project_template_root


class PreviewItem(BaseModel):
    path: str
    status: str
    reason: str = ""


class InstallPreview(BaseModel):
    codex_config_path: str
    codex_config_exists: bool
    agent_root: PreviewItem
    skill_root: str
    skill_root_candidates: list[dict[str, str]] = Field(default_factory=list)
    agent_files: list[PreviewItem] = Field(default_factory=list)
    skill_files: list[PreviewItem] = Field(default_factory=list)
    project_template_files: list[PreviewItem] = Field(default_factory=list)
    backups: list[str] = Field(default_factory=list)


_AGENT_FILES = ["atlas.toml", "scout.toml", "prometheus.toml", "hephaestus.toml", "oracle.toml", "librarian.toml"]
_SKILL_DIRS = ["codex-hermes-harness", "versioned-files", "official-llm-wiki"]
_PROJECT_TEMPLATE_FILES = [".codex/config.toml", "AGENTS.md", "README.md"]


def build_install_preview(config: SupervisorConfig, *, user_home: Path | None = None) -> InstallPreview:
    """Build a dry-run preview without modifying any files."""

    validate_generated_agent_templates(get_managed_agent_templates())
    home = user_home or paths.user_home()
    codex_config = home / ".codex" / "config.toml"
    agent_root = home / ".codex" / "agents"
    skill_root, skill_candidates = config.discover_skill_root(user_home=home)

    agent_status = "exists" if agent_root.exists() else "would_create"
    preview = InstallPreview(
        codex_config_path=normalize_windows_path(codex_config),
        codex_config_exists=codex_config.exists(),
        agent_root=PreviewItem(path=normalize_windows_path(agent_root), status=agent_status, reason="User-scoped custom agent root"),
        skill_root=normalize_windows_path(skill_root),
        skill_root_candidates=skill_candidates,
    )

    for file_name in _AGENT_FILES:
        preview.agent_files.append(
            PreviewItem(
                path=normalize_windows_path(agent_root / file_name),
                status="update" if (agent_root / file_name).exists() else "would_create",
                reason="Managed custom agent file",
            )
        )
    for skill_dir in _SKILL_DIRS:
        preview.skill_files.append(
            PreviewItem(
                path=normalize_windows_path(skill_root / skill_dir / "SKILL.md"),
                status="update" if (skill_root / skill_dir / "SKILL.md").exists() else "would_create",
                reason="Managed skill file",
            )
        )
    template_root = project_template_root()
    for template_file in _PROJECT_TEMPLATE_FILES:
        path = template_root / Path(template_file)
        preview.project_template_files.append(
            PreviewItem(
                path=normalize_windows_path(path),
                status="update" if path.exists() else "would_create",
                reason="Managed project-local template file",
            )
        )
    if codex_config.exists():
        preview.backups.append(normalize_windows_path(codex_config.with_name(codex_config.name + ".bak.<timestamp>")))
    return preview
