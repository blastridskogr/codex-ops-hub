"""Deterministic Git diff guard."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from codex_hermes_supervisor.integrations.git import GitChange, collect_git_changes, normalize_git_path, sha256_file, worktree_fingerprint
from codex_hermes_supervisor.schemas.errors import ViolationItem
from codex_hermes_supervisor.schemas.tools import ChangedFileItem, HarnessCheckData
from codex_hermes_supervisor.services.versioning import latest_version_path


def _normalize_paths(paths: list[str]) -> set[str]:
    return {normalize_git_path(path).lower() for path in paths}


def _path_is_within(parent: str, candidate: str) -> bool:
    parent_norm = normalize_git_path(parent).lower()
    candidate_norm = normalize_git_path(candidate).lower()
    return candidate_norm == parent_norm or candidate_norm.startswith(parent_norm + "/")


def _matches_forbidden(path: str, forbidden_patterns: list[str]) -> bool:
    normalized = normalize_git_path(path)
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in forbidden_patterns)


def _is_allowed_or_managed(path: str, *, allowed_files: set[str], managed_files: set[str]) -> bool:
    candidate = normalize_git_path(path).lower()
    if candidate in allowed_files or candidate in managed_files:
        return True

    prefixes = {item for item in allowed_files | managed_files if item.startswith(candidate + "/")}
    return bool(prefixes)


def _is_authorized_strict_write(path: str, strict_records: dict[str, str | None]) -> bool:
    candidate = normalize_git_path(path).lower()
    if candidate in strict_records:
        return True
    return any(record_path.startswith(candidate + "/") for record_path in strict_records)


def _change_matches_baseline(
    change: GitChange,
    repo_root: Path,
    baseline_hashes: dict[str, str],
    baseline_untracked: set[str],
) -> bool:
    if not change.affected_paths():
        return False
    if change.kind == "untracked":
        return all(any(_path_is_within(existing, rel_path) for existing in baseline_untracked) for rel_path in change.affected_paths())
    matched_any = False
    for rel_path in change.affected_paths():
        normalized = normalize_git_path(rel_path)
        if normalized not in baseline_hashes:
            return False
        matched_any = True
        current_hash = sha256_file(repo_root / normalized)
        if current_hash != baseline_hashes[normalized]:
            return False
    return matched_any


def _build_changed_item(change: GitChange) -> ChangedFileItem:
    path = change.new_path or change.path or change.old_path or ""
    return ChangedFileItem(path=path, status=change.kind, staged=change.staged)


def _check_pending_versions(state) -> tuple[list[ViolationItem], list[str], list[dict[str, object]]]:
    violations: list[ViolationItem] = []
    pending_versions: list[str] = []
    mirror_checks: list[dict[str, object]] = []
    for managed in state.managed_files:
        if managed.pending_version and not managed.synced:
            pending_versions.append(managed.pending_version)
            violations.append(
                ViolationItem(
                    type="pending_version_not_synced",
                    path=managed.active_path,
                    message=f"{managed.active_path} still has a pending unsynced version.",
                    severity="error",
                )
            )

        if managed.synced and managed.active_hash and managed.version_hash:
            active_path = Path(state.repo_root) / normalize_git_path(managed.active_path)
            latest = latest_version_path(active_path, mode=managed.versioning_mode)
            actual_active_hash = sha256_file(active_path)
            actual_version_hash = sha256_file(latest) if latest else None
            matches = actual_active_hash == actual_version_hash == managed.active_hash == managed.version_hash
            mirror_checks.append(
                {
                    "active_path": managed.active_path,
                    "latest_version": str(latest) if latest else None,
                    "matches": matches,
                    "active_hash": actual_active_hash,
                    "version_hash": actual_version_hash,
                }
            )
            if not matches:
                violations.append(
                    ViolationItem(
                        type="active_mirror_mismatch",
                        path=managed.active_path,
                        message=f"{managed.active_path} does not match its latest version snapshot.",
                        severity="error",
                    )
                )
    return violations, pending_versions, mirror_checks


def run_harness_check(
    repo_root: Path,
    state,
    *,
    config=None,
    include_staged: bool = True,
    include_untracked: bool = True,
) -> HarnessCheckData:
    """Run deterministic diff checks against the stored baseline."""

    current_changes = collect_git_changes(repo_root, include_staged=include_staged, include_untracked=include_untracked)
    baseline_untracked = _normalize_paths(state.baseline.untracked_files_at_start)
    task_changes: list[GitChange] = []
    for change in current_changes:
        if _change_matches_baseline(change, repo_root, state.baseline.file_hashes_at_start, baseline_untracked):
            continue
        task_changes.append(change)

    allowed_files = _normalize_paths(state.plan.allowed_files)
    managed_paths = _normalize_paths([managed.active_path for managed in state.managed_files] + [decl.active_path for decl in state.plan.managed_files])
    strict_records = {
        normalize_git_path(record.path).lower(): record.expected_hash
        for record in getattr(state, "strict_write_log", [])
    }
    violations: list[ViolationItem] = []

    forbidden_patterns = [normalize_git_path(path) for path in state.plan.forbidden_files]
    for change in task_changes:
        for rel_path in change.affected_paths():
            if _matches_forbidden(rel_path, forbidden_patterns):
                violations.append(
                    ViolationItem(
                        type="forbidden_pattern",
                        path=rel_path,
                        message=f"{rel_path} matches a forbidden file pattern.",
                        severity="error",
                    )
                )

            if not _is_allowed_or_managed(rel_path, allowed_files=allowed_files, managed_files=managed_paths):
                violations.append(
                    ViolationItem(
                        type="outside_allowed_files",
                        path=rel_path,
                        message=f"{rel_path} changed but was not listed in allowed_files or managed files.",
                        severity="error",
                    )
                )
            elif config is not None and config.strict_mode.enabled and config.strict_mode.write_policy == "supervisor_only":
                normalized = normalize_git_path(rel_path).lower()
                if not _is_authorized_strict_write(rel_path, strict_records):
                    violations.append(
                        ViolationItem(
                            type="write_without_supervisor",
                            path=rel_path,
                            message=f"{rel_path} changed under strict supervisor-only mode without a recorded supervisor write.",
                            severity="error",
                        )
                    )
                elif change.kind not in {"deleted", "renamed"}:
                    expected_hash = strict_records.get(normalized)
                    current_path = repo_root / normalize_git_path(rel_path)
                    current_hash = sha256_file(current_path) if current_path.exists() else None
                    if expected_hash is not None and current_hash is not None and current_hash != expected_hash:
                        violations.append(
                            ViolationItem(
                                type="direct_edit_detected",
                                path=rel_path,
                                message=f"{rel_path} no longer matches the last supervisor-written hash under strict mode.",
                                severity="error",
                            )
                        )

        if change.kind == "deleted" and not state.plan.delete_allowed:
            violations.append(
                ViolationItem(
                    type="delete_not_allowed",
                    path=change.path,
                    message=f"{change.path} was deleted without delete_allowed=true.",
                    severity="error",
                )
            )
        if change.kind == "renamed" and not state.plan.rename_allowed:
            target = change.new_path or change.path or ""
            violations.append(
                ViolationItem(
                    type="rename_not_allowed",
                    path=target,
                    message=f"Rename {change.old_path} -> {target} requires rename_allowed=true.",
                    severity="error",
                )
            )
        if change.kind.startswith("submodule") and not state.plan.allow_submodule_changes:
            violations.append(
                ViolationItem(
                    type="submodule_change_not_allowed",
                    path=change.path,
                    message="Submodule changes are high-risk and require allow_submodule_changes=true.",
                    severity="error",
                )
            )

    pending_violations, pending_versions, mirror_checks = _check_pending_versions(state)
    violations.extend(pending_violations)

    return HarnessCheckData(
        check_passed=not any(item.severity == "error" for item in violations),
        phase="CHECKED",
        changed_files=[_build_changed_item(change) for change in task_changes],
        violations=violations,
        pending_versions=pending_versions,
        active_mirror_checks=mirror_checks,
    )


def compute_finish_fingerprint(repo_root: Path, state) -> str:
    """Compute the worktree fingerprint used by harness_finish."""

    relevant = sorted({path for change in collect_git_changes(repo_root) for path in change.affected_paths()})
    return worktree_fingerprint(repo_root, relevant_paths=relevant)
