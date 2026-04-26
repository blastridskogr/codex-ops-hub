"""Windows-focused repository path guard."""

from __future__ import annotations

import os
import re
from pathlib import Path

_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")


class PathPolicyError(ValueError):
    """Raised when a path violates the contract."""


def _is_unc(path: str) -> bool:
    return path.startswith("\\\\")


def _is_extended(path: str) -> bool:
    return path.startswith("\\\\?\\")


def _has_ads(path: str) -> bool:
    if len(path) >= 2 and path[1] == ":" and path[:1].isalpha():
        remainder = path[2:]
        return ":" in remainder
    return ":" in path


def _normalize_input(user_path: str) -> str:
    if "\x00" in user_path:
        raise PathPolicyError("NUL byte is not allowed in paths.")
    if _CONTROL_CHARS.search(user_path):
        raise PathPolicyError("Control characters are not allowed in paths.")
    return user_path.replace("/", "\\")


def _split_components(path: str) -> list[str]:
    drive, tail = os.path.splitdrive(path)
    _ = drive
    return [component for component in tail.split("\\") if component not in ("", ".")]


def _reject_component(component: str) -> None:
    if component.endswith("."):
        raise PathPolicyError("Path components with trailing dot are forbidden.")
    if component.endswith(" "):
        raise PathPolicyError("Path components with trailing space are forbidden.")
    if component in {".."}:
        raise PathPolicyError("Path traversal is forbidden.")
    trimmed = component.rstrip(". ").split(".")[0].upper()
    if trimmed in _RESERVED_NAMES:
        raise PathPolicyError("Reserved Windows device names are forbidden.")


def _path_has_reparse_point(path: Path, repo_root: Path) -> bool:
    current = path
    while True:
        if current.exists():
            if current.is_symlink():
                return True
            isjunction = getattr(os.path, "isjunction", None)
            if callable(isjunction) and isjunction(str(current)):
                return True
        if current == repo_root:
            return False
        if current.parent == current:
            return False
        current = current.parent


def _is_within(path: Path, root: Path) -> bool:
    norm_path = os.path.normcase(str(path))
    norm_root = os.path.normcase(str(root))
    return os.path.commonpath([norm_path, norm_root]) == norm_root


def normalize_repo_path(
    repo_root: Path,
    user_path: str,
    *,
    allow_absolute: bool = True,
    allow_unc: bool = False,
    write: bool = False,
) -> Path:
    """Return a resolved absolute path inside repo_root."""

    normalized_input = _normalize_input(user_path.strip())
    if _is_extended(normalized_input):
        raise PathPolicyError("Extended paths are forbidden.")
    if _is_unc(normalized_input) and not allow_unc:
        raise PathPolicyError("UNC paths are forbidden.")
    if _has_ads(normalized_input):
        raise PathPolicyError("Alternate data streams are forbidden.")

    components = _split_components(normalized_input)
    for component in components:
        _reject_component(component)

    repo_root = repo_root.resolve()
    candidate = Path(normalized_input)
    if candidate.is_absolute():
        if not allow_absolute:
            raise PathPolicyError("Absolute paths are forbidden for this operation.")
        if os.path.normcase(candidate.drive) != os.path.normcase(repo_root.drive):
            raise PathPolicyError("Different drive letters are forbidden.")
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (repo_root / candidate).resolve(strict=False)

    if not _is_within(resolved, repo_root):
        raise PathPolicyError("Path escapes repo root.")
    if write and _path_has_reparse_point(resolved, repo_root):
        raise PathPolicyError("Writing through symlinks or junctions is forbidden.")
    return resolved
