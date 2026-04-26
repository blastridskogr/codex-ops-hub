"""Identity helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from codex_hermes_supervisor.schemas.state import IdentityModel

from .git_probe import git_common_dir, git_remote_origin_url, git_show_toplevel

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str, max_len: int = 40) -> str:
    collapsed = _SLUG_RE.sub("-", text.lower()).strip("-")
    return collapsed[:max_len] or "item"


def short_hash(text: str, size: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def normalize_windows_path(path: Path) -> str:
    return str(path.resolve()).replace("/", "\\")


def normalize_git_remote(remote: str) -> str:
    normalized = remote.strip().replace("\\", "/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.lower()


def project_name_from_remote(remote: str) -> str:
    normalized = normalize_git_remote(remote)
    return normalized.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def compute_workspace_id(repo_root: Path) -> str:
    worktree_root = git_show_toplevel(repo_root) or repo_root.resolve()
    normalized = normalize_windows_path(worktree_root)
    return f"{short_hash(normalized)}-{slug(Path(normalized).name)}"


def compute_project_id(repo_root: Path) -> tuple[str, str | None, str | None]:
    remote = git_remote_origin_url(repo_root)
    if remote:
        normalized_remote = normalize_git_remote(remote)
        return (
            f"{short_hash(normalized_remote)}-{slug(project_name_from_remote(remote))}",
            remote,
            None,
        )

    common = git_common_dir(repo_root)
    if common:
        normalized_common = normalize_windows_path(common)
        return (
            f"{short_hash(normalized_common)}-{slug(repo_root.resolve().name)}",
            None,
            normalized_common,
        )

    normalized_repo = normalize_windows_path(repo_root.resolve())
    return (
        f"{short_hash(normalized_repo)}-{slug(repo_root.resolve().name)}",
        None,
        None,
    )


def build_identity(repo_root: Path) -> IdentityModel:
    resolved_root = git_show_toplevel(repo_root) or repo_root.resolve()
    workspace_id = compute_workspace_id(resolved_root)
    project_id, remote, common_dir = compute_project_id(resolved_root)
    return IdentityModel(
        workspace_id=workspace_id,
        project_id=project_id,
        repo_root=normalize_windows_path(resolved_root),
        git_remote=remote,
        git_common_dir=common_dir,
    )
