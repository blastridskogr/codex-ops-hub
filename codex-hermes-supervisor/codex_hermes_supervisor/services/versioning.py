"""Managed file versioning helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from codex_hermes_supervisor.core.atomic_write import atomic_write_bytes, atomic_write_text
from codex_hermes_supervisor.schemas.versioning import (
    ManagedFileState,
    VersionPrepareData,
    VersionSyncData,
    VersioningMode,
)

_VERSION_RE = re.compile(r"^(?P<base>.+)_V(?P<major>\d+)\.(?P<minor>\d+)$")


class VersioningError(RuntimeError):
    """Raised for invalid managed-file transitions."""


def _split_name(path: Path) -> tuple[str, str]:
    return path.stem, path.suffix


def parse_versioned_path(path: Path) -> tuple[str, int, int, str] | None:
    stem, suffix = _split_name(path)
    match = _VERSION_RE.match(stem)
    if not match:
        return None
    return (
        match.group("base"),
        int(match.group("major")),
        int(match.group("minor")),
        suffix,
    )


def version_sort_key(path: Path) -> tuple[int, int]:
    parsed = parse_versioned_path(path)
    if parsed is None:
        raise VersioningError(f"Not a versioned file: {path}")
    _, major, minor, _ = parsed
    return major, minor


def latest_version_path(active_path: Path, *, mode: VersioningMode = "side_by_side") -> Path | None:
    candidates = list_version_paths(active_path, mode=mode)
    if not candidates:
        return None
    return sorted(candidates, key=version_sort_key)[-1]


def next_version_path(active_path: Path, *, mode: VersioningMode = "side_by_side") -> Path:
    latest = latest_version_path(active_path, mode=mode)
    if latest is None:
        return version_path_for(active_path, 0, 1, mode=mode)
    base, major, minor, suffix = parse_versioned_path(latest) or ("", 0, 0, "")
    return _version_path(active_path, base, major, minor + 1, suffix, mode)


def version_path_for(active_path: Path, major: int, minor: int, *, mode: VersioningMode = "side_by_side") -> Path:
    stem, suffix = _split_name(active_path)
    return _version_path(active_path, stem, major, minor, suffix, mode)


def _version_path(
    active_path: Path,
    base: str,
    major: int,
    minor: int,
    suffix: str,
    mode: VersioningMode,
) -> Path:
    if mode == "versions_dir":
        return active_path.parent / ".versions" / f"{base}_V{major}.{minor}{suffix}"
    return active_path.parent / f"{base}_V{major}.{minor}{suffix}"


def list_version_paths(active_path: Path, *, mode: VersioningMode = "side_by_side") -> list[Path]:
    if mode == "versions_dir":
        root = active_path.parent / ".versions"
    else:
        root = active_path.parent
    if not root.exists():
        return []
    stem, suffix = _split_name(active_path)
    pattern = f"{stem}_V*.?*{suffix}"
    results: list[Path] = []
    for path in root.glob(pattern):
        parsed = parse_versioned_path(path)
        if parsed and parsed[0] == stem:
            results.append(path)
    return results


def _sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def prepare_version(
    active_path: Path,
    *,
    mode: VersioningMode = "side_by_side",
    allow_create: bool = False,
    existing_state: ManagedFileState | None = None,
) -> tuple[VersionPrepareData, ManagedFileState]:
    """Prepare the next editable version snapshot."""

    existing_state = existing_state or ManagedFileState(active_path=str(active_path), versioning_mode=mode)
    if existing_state.pending_version:
        pending = Path(existing_state.pending_version)
        return (
            VersionPrepareData(
                active_path=str(active_path),
                version_path=str(pending),
                previous_version_path=str(existing_state.versions[-1]) if existing_state.versions else None,
                pending=True,
                sync_required=True,
                instruction=f"Edit only {pending}, then call version_sync.",
            ),
            existing_state,
        )

    latest = latest_version_path(active_path, mode=mode)
    if latest is None:
        if active_path.exists():
            first = version_path_for(active_path, 0, 1, mode=mode)
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(active_path.read_bytes())
            editable = version_path_for(active_path, 0, 2, mode=mode)
            editable.write_bytes(first.read_bytes())
            existing_state.versions = [str(first), str(editable)]
            existing_state.pending_version = str(editable)
            existing_state.synced = False
            return (
                VersionPrepareData(
                    active_path=str(active_path),
                    version_path=str(editable),
                    previous_version_path=str(first),
                    pending=True,
                    sync_required=True,
                    instruction=f"Edit only {editable}, then call version_sync.",
                ),
                existing_state,
            )
        if not allow_create:
            raise VersioningError("Active file does not exist and creation was not explicitly allowed.")
        editable = version_path_for(active_path, 0, 1, mode=mode)
        editable.parent.mkdir(parents=True, exist_ok=True)
        editable.write_bytes(b"")
        existing_state.versions = [str(editable)]
        existing_state.pending_version = str(editable)
        existing_state.synced = False
        return (
            VersionPrepareData(
                active_path=str(active_path),
                version_path=str(editable),
                previous_version_path=None,
                pending=True,
                sync_required=True,
                instruction=f"Edit only {editable}, then call version_sync.",
            ),
            existing_state,
        )

    editable = next_version_path(active_path, mode=mode)
    editable.parent.mkdir(parents=True, exist_ok=True)
    editable.write_bytes(latest.read_bytes())
    existing_state.versions = [*existing_state.versions, str(editable)] if existing_state.versions else [str(latest), str(editable)]
    existing_state.pending_version = str(editable)
    existing_state.synced = False
    return (
        VersionPrepareData(
            active_path=str(active_path),
            version_path=str(editable),
            previous_version_path=str(latest),
            pending=True,
            sync_required=True,
            instruction=f"Edit only {editable}, then call version_sync.",
        ),
        existing_state,
    )


def sync_version(
    active_path: Path,
    version_path: Path,
    *,
    existing_state: ManagedFileState | None = None,
) -> tuple[VersionSyncData, ManagedFileState]:
    """Copy version snapshot bytes into the active mirror."""

    existing_state = existing_state or ManagedFileState(active_path=str(active_path))
    if existing_state.pending_version and Path(existing_state.pending_version) != version_path:
        raise VersioningError("Version path does not match the pending version.")
    data = version_path.read_bytes()
    atomic_write_bytes(active_path, data)
    version_hash = _sha256_bytes(data)
    active_hash = _sha256_bytes(active_path.read_bytes())
    if version_hash != active_hash:
        raise VersioningError("ACTIVE_MIRROR_MISMATCH")

    existing_state.pending_version = None
    existing_state.synced = True
    existing_state.active_hash = active_hash
    existing_state.version_hash = version_hash
    if str(version_path) not in existing_state.versions:
        existing_state.versions.append(str(version_path))
    return (
        VersionSyncData(
            active_path=str(active_path),
            version_path=str(version_path),
            active_hash=active_hash,
            version_hash=version_hash,
            synced=True,
            pending=False,
        ),
        existing_state,
    )


def write_version_text(version_path: Path, content: str, *, encoding: str = "utf-8") -> int:
    """Atomically replace one pending version snapshot with new text content."""

    atomic_write_text(version_path, content, encoding=encoding)
    return len(content.encode(encoding))
