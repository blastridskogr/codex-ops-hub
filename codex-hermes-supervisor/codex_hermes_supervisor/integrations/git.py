"""Git integration helpers for baseline capture and diff inspection."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_hermes_supervisor.schemas.state import GitBaseline


class GitError(RuntimeError):
    """Raised when a git command fails."""


@dataclass(frozen=True)
class GitChange:
    """Normalized repository change item."""

    kind: str
    path: str | None = None
    staged: bool = False
    old_path: str | None = None
    new_path: str | None = None

    def affected_paths(self) -> list[str]:
        values = [self.path, self.old_path, self.new_path]
        return [value for value in values if value]


def normalize_git_path(path: str) -> str:
    """Normalize git-relative paths for Windows-safe comparison."""

    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # Under MCP stdio transport, git must not inherit the JSON-RPC stdin pipe or
    # attempt interactive credential prompts.
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git_bytes(args: list[str], cwd: Path) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            check=True,
            stdin=subprocess.DEVNULL,
            env=_git_env(),
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise GitError("git executable not found") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(stderr or f"git {' '.join(args)} failed") from exc
    return completed.stdout


def _run_git_text(args: list[str], cwd: Path) -> str:
    return _run_git_bytes(args, cwd).decode("utf-8", errors="replace").strip()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _split_z_bytes(data: bytes) -> list[str]:
    return [chunk.decode("utf-8", errors="replace") for chunk in data.split(b"\0") if chunk]


def parse_porcelain_v1_z(data: bytes) -> list[GitChange]:
    """Parse `git status --porcelain=v1 -z` output into normalized changes."""

    entries = _split_z_bytes(data)
    index = 0
    changes: list[GitChange] = []
    while index < len(entries):
        entry = entries[index]
        index += 1
        if entry in {"??", "!!"}:
            continue

        xy = entry[:2]
        path = normalize_git_path(entry[3:])
        rename_target: str | None = None
        if "R" in xy or "C" in xy:
            if index >= len(entries):
                raise GitError("Malformed porcelain rename entry.")
            rename_target = normalize_git_path(entries[index])
            index += 1

        x, y = xy[0], xy[1]

        if x not in {" ", "?"}:
            changes.append(_change_from_status(x, path, staged=True, rename_target=rename_target))
        if y not in {" ", "?"}:
            changes.append(_change_from_status(y, path, staged=False, rename_target=rename_target))
        if xy == "??":
            changes.append(GitChange(kind="untracked", path=path, staged=False))
    return _dedupe_changes(changes)


def _change_from_status(status: str, path: str, *, staged: bool, rename_target: str | None) -> GitChange:
    if status == "R":
        return GitChange(kind="renamed", path=rename_target or path, old_path=path, new_path=rename_target or path, staged=staged)
    if status == "D":
        return GitChange(kind="deleted", path=path, staged=staged)
    if status == "T":
        return GitChange(kind="typechange", path=path, staged=staged)
    if status in {"A", "C"}:
        return GitChange(kind="modified", path=rename_target or path, staged=staged)
    if status in {"M", "U"}:
        return GitChange(kind="modified", path=path, staged=staged)
    return GitChange(kind="modified", path=path, staged=staged)


def _dedupe_changes(changes: list[GitChange]) -> list[GitChange]:
    seen: set[tuple[str, str | None, str | None, str | None, bool]] = set()
    deduped: list[GitChange] = []
    for change in changes:
        key = (change.kind, change.path, change.old_path, change.new_path, change.staged)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(change)
    return deduped


def capture_git_baseline(repo_root: Path) -> GitBaseline:
    """Capture the baseline git state for a new harness task."""

    head = _run_git_text(["rev-parse", "HEAD"], repo_root)
    porcelain = parse_porcelain_v1_z(_run_git_bytes(["status", "--porcelain=v1", "-z"], repo_root))
    dirty_files = sorted({path for change in porcelain if change.kind != "untracked" for path in change.affected_paths()})
    untracked = sorted({change.path for change in porcelain if change.kind == "untracked" and change.path})
    hashes: dict[str, str] = {}
    for rel_path in dirty_files:
        file_hash = sha256_file(repo_root / rel_path)
        if file_hash:
            hashes[rel_path] = file_hash
    submodule_status = _run_git_text(["submodule", "status", "--recursive"], repo_root)
    return GitBaseline(
        head=head,
        dirty_files_at_start=dirty_files,
        untracked_files_at_start=untracked,
        file_hashes_at_start=hashes,
        submodule_status_at_start=submodule_status or None,
    )


def list_untracked(repo_root: Path) -> list[GitChange]:
    items = _split_z_bytes(_run_git_bytes(["ls-files", "--others", "--exclude-standard", "-z"], repo_root))
    return [GitChange(kind="untracked", path=normalize_git_path(item), staged=False) for item in items]


def collect_git_changes(
    repo_root: Path,
    *,
    include_staged: bool = True,
    include_untracked: bool = True,
) -> list[GitChange]:
    """Collect current repository changes using porcelain output plus submodule status."""

    changes = parse_porcelain_v1_z(_run_git_bytes(["status", "--porcelain=v1", "-z"], repo_root))
    if not include_staged:
        changes = [change for change in changes if not change.staged]
    if not include_untracked:
        changes = [change for change in changes if change.kind != "untracked"]
    submodule_changes = detect_submodule_changes(repo_root)
    return _dedupe_changes([*changes, *submodule_changes])


def detect_submodule_changes(repo_root: Path, baseline_status: str | None = None) -> list[GitChange]:
    """Return submodule change items by comparing current submodule status."""

    current = _run_git_text(["submodule", "status", "--recursive"], repo_root) or ""
    if baseline_status is not None and current == (baseline_status or ""):
        return []

    changes: list[GitChange] = []
    current_lines = [line for line in current.splitlines() if line.strip()]
    for line in current_lines:
        marker = line[0]
        remainder = line[1:].strip()
        if not remainder:
            continue
        parts = remainder.split()
        if len(parts) < 2:
            continue
        path = normalize_git_path(parts[1])
        changes.append(GitChange(kind="submodulechange", path=path, staged=False))
        if marker in {"+", "-"}:
            changes.append(GitChange(kind="submodule_head_changed", path=path, staged=False))
        if marker == "U":
            changes.append(GitChange(kind="submodule_dirty_state_changed", path=path, staged=False))
    return _dedupe_changes(changes)


def worktree_fingerprint(repo_root: Path, *, relevant_paths: list[str] | None = None) -> str:
    """Compute a deterministic SHA256 fingerprint of git-visible worktree state."""

    status_bytes = _run_git_bytes(["status", "--porcelain=v1", "-z"], repo_root)
    diff_bytes = _run_git_bytes(["diff", "--name-status", "-z"], repo_root)
    cached_bytes = _run_git_bytes(["diff", "--cached", "--name-status", "-z"], repo_root)

    if relevant_paths is None:
        relevant_paths = sorted({path for change in collect_git_changes(repo_root) for path in change.affected_paths()})
    else:
        relevant_paths = sorted({normalize_git_path(path) for path in relevant_paths})

    digest = hashlib.sha256()
    digest.update(status_bytes)
    digest.update(b"\0SECTION\0")
    digest.update(diff_bytes)
    digest.update(b"\0SECTION\0")
    digest.update(cached_bytes)
    digest.update(b"\0SECTION\0")
    for rel_path in relevant_paths:
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        file_path = repo_root / rel_path
        file_hash = sha256_file(file_path)
        digest.update((file_hash or "<missing>").encode("utf-8"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"
