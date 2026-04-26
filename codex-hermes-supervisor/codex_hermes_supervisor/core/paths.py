"""Path helpers for user-scoped Codex and supervisor state."""

from __future__ import annotations

from pathlib import Path


def user_home() -> Path:
    return Path.home()


def codex_root() -> Path:
    return user_home() / ".codex"


def codex_config_path() -> Path:
    return codex_root() / "config.toml"


def codex_agents_root() -> Path:
    return codex_root() / "agents"


def codex_skills_root() -> Path:
    return codex_root() / "skills"


def agents_skills_root() -> Path:
    return user_home() / ".agents" / "skills"


def supervisor_root() -> Path:
    return user_home() / ".codex-hermes"


def supervisor_config_path() -> Path:
    return supervisor_root() / "config.yaml"


def supervisor_state_root() -> Path:
    return supervisor_root() / "state"


def supervisor_logs_root() -> Path:
    return supervisor_root() / "logs"


def supervisor_cache_root() -> Path:
    return supervisor_root() / "cache"


def hermes_outbox_root() -> Path:
    return supervisor_root() / "hermes_outbox"


def supervisor_projects_root() -> Path:
    return supervisor_root() / "projects"
