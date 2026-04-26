"""Project-level Git bootstrap helpers.

The supervisor uses Git for workspace identity, change tracking, and finish
gates. This module makes non-Git project folders usable without staging or
committing arbitrary user files.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.git_probe import git_show_toplevel
from codex_hermes_supervisor.core.identity import normalize_windows_path


MANAGED_GITIGNORE_BEGIN = "# BEGIN CODEX-HERMES PROJECT GIT BOOTSTRAP"
MANAGED_GITIGNORE_END = "# END CODEX-HERMES PROJECT GIT BOOTSTRAP"
MANAGED_GITIGNORE_BODY = [
    MANAGED_GITIGNORE_BEGIN,
    ".env",
    ".env.*",
    "secrets/",
    "**/secrets/",
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
    MANAGED_GITIGNORE_END,
]


class ProjectGitBootstrapError(RuntimeError):
    """Raised when a project cannot be safely prepared as a Git repo."""


@dataclass(frozen=True)
class ProjectGitBootstrapResult:
    repo_root: str
    git_root: str | None
    already_git_repo: bool
    initialized: bool = False
    gitignore_changed: bool = False
    local_identity_configured: bool = False
    initial_commit_created: bool = False
    head: str | None = None
    dry_run: bool = False
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "git_root": self.git_root,
            "already_git_repo": self.already_git_repo,
            "initialized": self.initialized,
            "gitignore_changed": self.gitignore_changed,
            "local_identity_configured": self.local_identity_configured,
            "initial_commit_created": self.initial_commit_created,
            "head": self.head,
            "dry_run": self.dry_run,
            "actions": self.actions,
            "warnings": self.warnings,
        }


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            check=check,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            env=_git_env(),
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise ProjectGitBootstrapError("git executable not found") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip() or f"git {' '.join(args)} failed"
        raise ProjectGitBootstrapError(message) from exc


def _has_head(repo_root: Path) -> tuple[bool, str | None]:
    completed = _run_git(["rev-parse", "--verify", "HEAD"], repo_root, check=False)
    if completed.returncode != 0:
        return False, None
    head = completed.stdout.strip()
    return bool(head), head or None


def _git_config_get(repo_root: Path, key: str) -> str | None:
    completed = _run_git(["config", "--local", "--get", key], repo_root, check=False)
    value = completed.stdout.strip()
    return value or None


def _staged_paths(repo_root: Path) -> list[str]:
    completed = _run_git(["diff", "--cached", "--name-only", "-z"], repo_root, check=False)
    if completed.returncode != 0:
        return []
    return [item for item in completed.stdout.split("\0") if item]


def _ensure_local_identity(repo_root: Path, *, dry_run: bool) -> tuple[bool, list[str]]:
    actions: list[str] = []
    changed = False
    if not _git_config_get(repo_root, "user.email"):
        actions.append("set local git user.email to codex-hermes@local.invalid")
        changed = True
        if not dry_run:
            _run_git(["config", "--local", "user.email", "codex-hermes@local.invalid"], repo_root)
    if not _git_config_get(repo_root, "user.name"):
        actions.append("set local git user.name to Codex-Hermes")
        changed = True
        if not dry_run:
            _run_git(["config", "--local", "user.name", "Codex-Hermes"], repo_root)
    return changed, actions


def _needs_gitignore_block(text: str) -> bool:
    return MANAGED_GITIGNORE_BEGIN not in text or MANAGED_GITIGNORE_END not in text


def _ensure_gitignore(repo_root: Path, *, dry_run: bool) -> tuple[bool, list[str]]:
    path = repo_root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if not _needs_gitignore_block(current):
        return False, []

    block = "\n".join(MANAGED_GITIGNORE_BODY) + "\n"
    updated = current
    if updated and not updated.endswith("\n"):
        updated += "\n"
    if updated:
        updated += "\n"
    updated += block
    if not dry_run:
        atomic_write_text(path, updated)
    return True, ["write conservative .gitignore managed block"]


def _guard_safe_project_root(repo_root: Path) -> None:
    resolved = repo_root.resolve()
    if not resolved.exists():
        raise ProjectGitBootstrapError(f"Project path does not exist: {normalize_windows_path(resolved)}")
    if not resolved.is_dir():
        raise ProjectGitBootstrapError(f"Project path is not a directory: {normalize_windows_path(resolved)}")
    anchor = resolved.anchor
    if resolved == Path(anchor):
        raise ProjectGitBootstrapError("Refusing to initialize Git at filesystem root.")
    home = Path.home().resolve()
    if resolved == home:
        raise ProjectGitBootstrapError("Refusing to initialize Git directly at the user home directory.")
    forbidden = [Path(os.environ.get("WINDIR", r"C:\Windows")).resolve()]
    if any(resolved == item or item in resolved.parents for item in forbidden):
        raise ProjectGitBootstrapError("Refusing to initialize Git inside the Windows system directory.")


def ensure_project_git(
    repo_root: Path,
    *,
    dry_run: bool = False,
    create_initial_commit: bool = True,
) -> ProjectGitBootstrapResult:
    """Ensure a project folder has a usable Git baseline.

    Existing repositories with a HEAD commit are returned unchanged. Non-Git
    folders are initialized. Empty repositories get an initial commit containing
    only the managed .gitignore block, or an empty commit if the block already
    exists. The function never runs `git add .`.
    """

    repo_root = repo_root.resolve()
    _guard_safe_project_root(repo_root)

    existing_git_root = git_show_toplevel(repo_root)
    already_git = existing_git_root is not None
    actions: list[str] = []
    warnings: list[str] = []
    initialized = False

    if already_git:
        git_root = existing_git_root.resolve()
    else:
        git_root = repo_root
        actions.append("git init")
        initialized = True
        if not dry_run:
            _run_git(["init"], repo_root)

    has_head = False
    head: str | None = None
    if not dry_run:
        has_head, head = _has_head(git_root)
    elif already_git:
        has_head, head = _has_head(git_root)

    if already_git and has_head:
        return ProjectGitBootstrapResult(
            repo_root=normalize_windows_path(repo_root),
            git_root=normalize_windows_path(git_root),
            already_git_repo=True,
            head=head,
            dry_run=dry_run,
            actions=actions,
            warnings=warnings,
        )

    gitignore_changed = False
    local_identity_changed = False
    initial_commit_created = False

    if create_initial_commit:
        staged_before = [path for path in _staged_paths(git_root) if path != ".gitignore"]
        if staged_before:
            raise ProjectGitBootstrapError(
                "Refusing to create initial baseline commit while non-.gitignore paths are already staged: "
                + ", ".join(staged_before[:10])
            )
        gitignore_changed, gitignore_actions = _ensure_gitignore(git_root, dry_run=dry_run)
        actions.extend(gitignore_actions)
        identity_changed, identity_actions = _ensure_local_identity(git_root, dry_run=dry_run)
        local_identity_changed = identity_changed
        actions.extend(identity_actions)
        actions.append("create initial Codex-Hermes Git baseline commit without staging arbitrary files")
        initial_commit_created = True
        if not dry_run:
            if gitignore_changed:
                _run_git(["add", "--", ".gitignore"], git_root)
            _run_git(["commit", "--allow-empty", "-m", "Initialize Codex-Hermes project baseline"], git_root)
            has_head, head = _has_head(git_root)
    else:
        warnings.append("Repository has no HEAD commit; harness_begin still requires an initial commit.")

    return ProjectGitBootstrapResult(
        repo_root=normalize_windows_path(repo_root),
        git_root=normalize_windows_path(git_root),
        already_git_repo=already_git,
        initialized=initialized,
        gitignore_changed=gitignore_changed,
        local_identity_configured=local_identity_changed,
        initial_commit_created=initial_commit_created,
        head=head,
        dry_run=dry_run,
        actions=actions,
        warnings=warnings,
    )
