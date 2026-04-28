"""Harness orchestration services."""

from __future__ import annotations

import hashlib
from pathlib import Path

from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.archive import archive_active_state
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.idempotency import IdempotencyMismatchError, natural_key, record_result, validate_existing
from codex_hermes_supervisor.core.identity import build_identity
from codex_hermes_supervisor.core.locks import WorkspaceLock, now_local_iso
from codex_hermes_supervisor.core.path_guard import PathPolicyError, normalize_repo_path
from codex_hermes_supervisor.core.state_machine import require_transition
from codex_hermes_supervisor.core.state_store import StateStoreError, WorkspaceStateStore
from codex_hermes_supervisor.integrations.git import capture_git_baseline, sha256_file
from codex_hermes_supervisor.integrations.hermes import HermesDirectModeError, append_direct_memory, build_recall_bundle, write_outbox_note
from codex_hermes_supervisor.integrations.obsidian import write_wiki_note
from codex_hermes_supervisor.schemas.errors import ErrorItem, ViolationItem
from codex_hermes_supervisor.schemas.responses import ResponseEnvelope
from codex_hermes_supervisor.schemas.state import FinishWritebackState, PlanMemoryContext, StrictWriteRecord, TaskState
from codex_hermes_supervisor.schemas.tools import (
    HarnessBeginData,
    HarnessBeginInput,
    HarnessApplyPatchData,
    HarnessApplyPatchInput,
    HarnessCheckpointData,
    HarnessCheckpointInput,
    HarnessCheckInput,
    HarnessFinishData,
    HarnessFinishInput,
    HarnessPlanData,
    HarnessPlanInput,
    HarnessWriteVersionData,
    HarnessWriteVersionInput,
    HermesRecallData,
    HermesRecallSource,
    LessonCaptureData,
    LessonCaptureInput,
    TaskRecordSummary,
    VersionPrepareInput,
    VersionSyncInput,
)
from codex_hermes_supervisor.schemas.verification import normalize_verification_results
from codex_hermes_supervisor.schemas.verification import VerificationResult, VerificationStep
from codex_hermes_supervisor.schemas.versioning import ManagedFileState
from codex_hermes_supervisor.schemas.wiki import WikiNoteInput
from codex_hermes_supervisor.security.secret_scan import SecretScanError, ensure_safe_text
from codex_hermes_supervisor.services.git_guard import compute_finish_fingerprint, run_harness_check
from codex_hermes_supervisor.services.project_git import ProjectGitBootstrapError, ensure_project_git
from codex_hermes_supervisor.services.project_memory import load_project_context
from codex_hermes_supervisor.services.task_records import append_lesson, append_worklog_entry, build_task_record_summary, ensure_task_records
from codex_hermes_supervisor.services.versioning import prepare_version, sync_version, write_version_text


def _slug(text: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe[:40] or "task"


def _random_suffix() -> str:
    return hashlib.sha256(now_local_iso().encode("utf-8")).hexdigest()[:6]


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


def make_task_id(task_title: str) -> str:
    return f"{now_local_iso().replace(':', '').replace('+', '').replace('-', '')[:15]}-{_slug(task_title)}-{_random_suffix()}"


def _error(tool: str, code: str, message: str, *, path: str | None = None) -> ResponseEnvelope[object]:
    return ResponseEnvelope.failure(tool=tool, errors=[ErrorItem(code=code, message=message, path=path)])


def _workspace_store(config: SupervisorConfig, workspace_id: str) -> WorkspaceStateStore:
    return WorkspaceStateStore(config.state_root, workspace_id)


def _lock_for(store: WorkspaceStateStore, state: TaskState | None, operation: str, config: SupervisorConfig) -> WorkspaceLock:
    task_id = state.task_id if state else "<unknown>"
    return WorkspaceLock(
        store.workspace_dir / "lock.json",
        workspace_id=store.workspace_id,
        task_id=task_id,
        operation=operation,
        timeout_seconds=config.state.lock_timeout_seconds,
        heartbeat_seconds=config.state.heartbeat_seconds,
    )


def _canonical_plan_verification_steps(payload: HarnessPlanInput) -> list[VerificationStep]:
    steps = list(payload.verification_steps)
    existing_test_commands = {step.command for step in steps if step.kind == "test" and step.command}
    for command in payload.tests_required:
        if command not in existing_test_commands:
            steps.append(
                VerificationStep(
                    kind="test",
                    command=command,
                    required=True,
                    reason="Derived from legacy tests_required.",
                )
            )
    return steps


def _verification_matches_step(result: VerificationResult, step: VerificationStep) -> bool:
    if result.kind != step.kind:
        return False
    if step.command:
        return result.command == step.command and result.status == "passed"
    return result.status == "passed"


def _verification_violations(state: TaskState, finish_even_with_warnings: bool) -> list[ViolationItem]:
    steps = list(state.plan.verification_steps)
    if state.plan.risk_level in {"medium", "high"} and not any(step.required for step in steps):
        return [
            ViolationItem(
                type="required_verification_plan_missing",
                message="Medium/high risk tasks require at least one required verification step.",
                severity="error",
            )
        ]

    violations: list[ViolationItem] = []
    for index, step in enumerate(steps):
        if not step.required:
            continue
        satisfied = False
        for result in state.verification_results:
            if result.satisfies_step == index and result.status == "passed":
                satisfied = True
                break
            if _verification_matches_step(result, step):
                satisfied = True
                break
        if satisfied:
            continue

        severity = "error"
        if state.plan.risk_level == "low" and finish_even_with_warnings:
            severity = "warning"
        violations.append(
            ViolationItem(
                type="required_verification_missing",
                message=f"Required verification step {index} ({step.kind}) was not satisfied.",
                severity=severity,
            )
        )
    return violations


def _is_allowed_path(target_path: str, allowed_files: list[str]) -> bool:
    target = target_path.lower()
    for allowed in allowed_files:
        allowed_norm = allowed.replace("\\", "/").strip("/").lower()
        if not allowed_norm:
            continue
        if target == allowed_norm or target.startswith(allowed_norm + "/"):
            return True
    return False


def _strict_mode_error(tool: str, code: str, message: str, *, path: str | None = None) -> ResponseEnvelope[object]:
    return _error(tool, code, message, path=path)


def _record_strict_write(state: TaskState, *, path: str, tool: str, expected_hash: str | None) -> None:
    record = StrictWriteRecord(
        path=path,
        tool=tool,
        expected_hash=expected_hash,
        recorded_at=now_local_iso(),
    )
    for index, existing in enumerate(state.strict_write_log):
        if existing.path == path:
            state.strict_write_log[index] = record
            return
    state.strict_write_log.append(record)


def _require_write_through_mode(config: SupervisorConfig, *, tool: str, allow_apply_patch: bool = False, allow_managed_write: bool = False) -> ResponseEnvelope[object] | None:
    strict = config.strict_mode
    if not strict.enabled or strict.write_policy != "supervisor_only":
        return _strict_mode_error(tool, "STRICT_MODE_NOT_ENABLED", "Strict supervisor write-through mode is not enabled.")
    if allow_apply_patch and not strict.allow_supervisor_apply_patch:
        return _strict_mode_error(tool, "STRICT_APPLY_PATCH_DISABLED", "Supervisor patch writes are disabled by config.")
    if allow_managed_write and not strict.allow_supervisor_managed_file_write:
        return _strict_mode_error(tool, "STRICT_MANAGED_WRITE_DISABLED", "Supervisor managed-file writes are disabled by config.")
    return None


def harness_begin(config: SupervisorConfig, payload: HarnessBeginInput) -> ResponseEnvelope[HarnessBeginData]:
    repo_root = Path(payload.repo_root).resolve()
    if not repo_root.exists():
        return _error("harness_begin", "GIT_NOT_REPO", "Repository root does not exist.", path=payload.repo_root)

    try:
        ensure_project_git(repo_root)
    except ProjectGitBootstrapError as exc:
        return _error(
            "harness_begin",
            "PROJECT_GIT_BOOTSTRAP_FAILED",
            f"Project Git bootstrap failed: {exc}",
            path=payload.repo_root,
        )

    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)

    existing_state: TaskState | None = None
    try:
        existing_state = store.load_state()
    except StateStoreError:
        existing_state = None

    provisional_task_id = make_task_id(payload.task)
    with WorkspaceLock(
        store.workspace_dir / "lock.json",
        workspace_id=identity.workspace_id,
        task_id=existing_state.task_id if existing_state else provisional_task_id,
        operation="harness_begin",
        timeout_seconds=config.state.lock_timeout_seconds,
        heartbeat_seconds=config.state.heartbeat_seconds,
    ):
        if existing_state and existing_state.phase not in {"FINISHED", "ARCHIVED"}:
            key = natural_key("harness_begin", identity.workspace_id, payload.task, str(payload.force_new_task))
            if payload.idempotency_key:
                key = natural_key("harness_begin", identity.workspace_id, payload.idempotency_key)
            try:
                existing = validate_existing(store, key, payload.model_dump())
            except IdempotencyMismatchError:
                return _error("harness_begin", "IDEMPOTENCY_PAYLOAD_MISMATCH", "Idempotency payload mismatch.")
            if existing:
                return ResponseEnvelope.success(tool="harness_begin", data=HarnessBeginData.model_validate(existing.result))
            if not payload.force_new_task:
                return _error("harness_begin", "ACTIVE_TASK_EXISTS", "Active task already exists for this workspace.")
            archive_active_state(store, existing_state, reason="force_new_task")

        if existing_state and existing_state.phase == "FINISHED":
            archive_active_state(store, existing_state, reason="finished_task_replaced")

        task_id = provisional_task_id
        baseline = capture_git_baseline(repo_root)
        ensure_task_records(store.tasks_dir)
        recall_bundle = build_recall_bundle(
            config,
            config.hermes_outbox_root,
            store.tasks_dir,
            current_project_id=identity.project_id,
            current_workspace_id=identity.workspace_id,
        )
        recall = recall_bundle.recall
        recall_sources = list(recall_bundle.sources)
        project_context = load_project_context(identity.project_id)
        if project_context and project_context.status == "reviewed":
            project_lines = [f"Project baseline: {project_context.compact_summary}"]
            if project_context.project_note:
                project_lines.append(f"Project note: {project_context.project_note}")
            if project_context.stale:
                project_lines.extend(project_context.stale_reasons)
            project_block = "\n".join(project_lines)
            current_len = len(recall)
            separator_len = 2 if recall else 0
            remaining = max(config.hermes.builtin_recall_max_total_chars - current_len - separator_len, 0)
            project_excerpt, project_truncated = _excerpt_text(
                project_block,
                remaining if remaining else len(project_block),
                config.hermes.builtin_recall_overflow_policy,
            )
            recall = f"{project_excerpt}\n\n{recall}".strip() if recall else project_excerpt
            recall_sources.append(
                HermesRecallSource(
                    source_type="project_registry",
                    path=project_context.manifest_path,
                    included_chars=len(project_excerpt),
                    original_chars=len(project_block),
                    truncated=project_truncated,
                    status="used",
                    overflow_policy=config.hermes.builtin_recall_overflow_policy if project_truncated else None,
                )
            )
        state = TaskState(
            task_id=task_id,
            idempotency_key=payload.idempotency_key,
            task=payload.task,
            repo_root=identity.repo_root,
            workspace_id=identity.workspace_id,
            project_id=identity.project_id,
            phase="STARTED",
            created_at=now_local_iso(),
            updated_at=now_local_iso(),
            baseline=baseline,
            hermes_recall_done=bool(recall),
        )
        store.save_state(state)
        summary = build_task_record_summary(store.tasks_dir)
        data = HarnessBeginData(
            task_id=task_id,
            identity=identity,
            phase="STARTED",
            baseline=baseline,
            hermes=HermesRecallData(
                requested_mode=recall_bundle.requested_mode,
                actual_mode=recall_bundle.actual_mode,
                fallback=recall_bundle.fallback,
                fallback_reason=recall_bundle.fallback_reason,
                recall_available=bool(recall),
                recall=recall,
                sources=recall_sources,
                recall_filter=recall_bundle.recall_filter,
            ),
            task_record_summary=summary,
            next_required_tool="harness_plan",
        )
        key = natural_key("harness_begin", identity.workspace_id, payload.idempotency_key or payload.task, str(payload.force_new_task))
        record_result(store, key=key, tool="harness_begin", task_id=task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="harness_begin", data=data)


def harness_plan(config: SupervisorConfig, payload: HarnessPlanInput) -> ResponseEnvelope[HarnessPlanData]:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("harness_plan", "STATE_NOT_FOUND", "No active state found for this workspace.")

    with _lock_for(store, state, "harness_plan", config):
        require_transition(state.phase, "PLANNED")
        preflight_required = config.memory_policy.require_memory_preflight_for_plan or payload.require_memory_preflight
        memory_context = PlanMemoryContext(preflight_required=preflight_required)
        if preflight_required and payload.memory_preflight is None:
            return _error(
                "harness_plan",
                "MEMORY_PREFLIGHT_REQUIRED",
                "memory_preflight is required before planning in the active memory profile.",
            )
        if payload.memory_preflight is not None:
            preflight = payload.memory_preflight
            if preflight.project_id != state.project_id:
                return _error(
                    "harness_plan",
                    "MEMORY_PREFLIGHT_PROJECT_MISMATCH",
                    "memory_preflight project_id does not match the active task project_id.",
                )
            if Path(preflight.repo_root).resolve() != repo_root:
                return _error(
                    "harness_plan",
                    "MEMORY_PREFLIGHT_REPO_MISMATCH",
                    "memory_preflight repo_root does not match the active task repo_root.",
                )
            if preflight.blockers:
                return _error(
                    "harness_plan",
                    "MEMORY_PREFLIGHT_HAS_BLOCKERS",
                    "memory_preflight contains blockers that must be resolved before planning.",
                )
            if preflight.memory_decision != "no_memory_needed" and not preflight.lookup_ran:
                return _error(
                    "harness_plan",
                    "MEMORY_PREFLIGHT_LOOKUP_NOT_RUN",
                    "memory_preflight lookup did not run for a lookup-required memory decision.",
                )
            memory_context = PlanMemoryContext(
                preflight_required=preflight_required,
                query=preflight.query,
                memory_decision=preflight.memory_decision,
                skip_reason=preflight.skip_reason,
                lookup_required=preflight.lookup_required,
                lookup_ran=preflight.lookup_ran,
                memory_evidence_ready=preflight.memory_evidence_ready,
                source_paths=preflight.source_paths,
                rejected_reference_paths=preflight.rejected_reference_paths,
                workstream_id=preflight.workstream_id,
                warnings=preflight.warnings,
                blockers=preflight.blockers,
            )
        state.plan.allowed_files = payload.allowed_files
        state.plan.forbidden_files = payload.forbidden_files
        state.plan.managed_files = payload.managed_files
        state.plan.tests_required = payload.tests_required
        state.plan.verification_steps = _canonical_plan_verification_steps(payload)
        state.plan.risk_level = payload.risk_level  # validated later by schema
        state.plan.plan_summary = payload.plan_summary
        state.plan.delete_allowed = payload.delete_allowed
        state.plan.rename_allowed = payload.rename_allowed
        state.plan.allow_submodule_changes = payload.allow_submodule_changes
        state.plan.memory_context = memory_context
        state.plan.plan_revision += 1
        state.phase = "PLANNED"
        state.updated_at = now_local_iso()
        store.save_state(state)
        data = HarnessPlanData(phase=state.phase, plan=state.plan, next_required_tool="harness_check")
        key = natural_key("harness_plan", state.task_id, payload.idempotency_key or str(state.plan.plan_revision))
        record_result(store, key=key, tool="harness_plan", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="harness_plan", data=data)


def harness_check(config: SupervisorConfig, payload: HarnessCheckInput) -> ResponseEnvelope:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("harness_check", "STATE_NOT_FOUND", "No active state found for this workspace.")

    with _lock_for(store, state, "harness_check", config):
        result = run_harness_check(
            repo_root,
            state,
            config=config,
            include_staged=payload.include_staged,
            include_untracked=payload.include_untracked,
        )
        state.phase = "CHECKED"
        state.changed_files = [item.path for item in result.changed_files]
        state.violations = result.violations
        state.updated_at = now_local_iso()
        store.save_state(state)
        key = natural_key("harness_check", state.task_id, payload.idempotency_key or str(payload.include_staged), str(payload.include_untracked))
        record_result(store, key=key, tool="harness_check", task_id=state.task_id, payload=payload.model_dump(), result=result.model_dump())
        return ResponseEnvelope.success(tool="harness_check", data=result)


def harness_checkpoint_tool(config: SupervisorConfig, payload: HarnessCheckpointInput) -> ResponseEnvelope[HarnessCheckpointData]:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("harness_checkpoint", "STATE_NOT_FOUND", "No active state found for this workspace.")

    try:
        ensure_safe_text(payload.summary, *payload.evidence, *( [payload.next_action] if payload.next_action else [] ))
    except SecretScanError:
        return _error("harness_checkpoint", "SECRET_DETECTED", "Sensitive or raw content cannot be persisted.")

    with _lock_for(store, state, "harness_checkpoint", config):
        lines = [f"### Checkpoint: {payload.kind}", "", payload.summary.strip()]
        if payload.evidence:
            lines.append("")
            lines.append("Evidence:")
            lines.extend(f"- {item.strip()}" for item in payload.evidence if item.strip())
        if payload.next_action:
            lines.append("")
            lines.append(f"Next action: {payload.next_action.strip()}")
        entry = "\n".join(lines).rstrip()
        worklog_path = append_worklog_entry(store.tasks_dir, entry, source="harness_checkpoint")
        state.updated_at = now_local_iso()
        store.save_state(state)
        data = HarnessCheckpointData(recorded=True, worklog_path=str(worklog_path), entry=entry)
        key = natural_key("harness_checkpoint", state.task_id, payload.idempotency_key or payload.kind, payload.summary)
        record_result(store, key=key, tool="harness_checkpoint", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="harness_checkpoint", data=data)


def version_prepare_tool(config: SupervisorConfig, payload: VersionPrepareInput) -> ResponseEnvelope:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("version_prepare", "STATE_NOT_FOUND", "No active state found for this workspace.")

    with _lock_for(store, state, "version_prepare", config):
        active_path = normalize_repo_path(repo_root, payload.active_path, write=False)
        normalized_active = active_path.relative_to(repo_root).as_posix()
        declared = {item.active_path for item in state.plan.managed_files}
        if normalized_active not in declared and active_path.parent != store.tasks_dir:
            return _error("version_prepare", "MANAGED_FILE_NOT_DECLARED", "Managed file must be declared before versioning.", path=normalized_active)

        existing = next((item for item in state.managed_files if item.active_path == normalized_active), None)
        data, managed_state = prepare_version(
            active_path,
            mode=payload.versioning_mode,
            allow_create=True,
            existing_state=existing,
        )
        managed_state.active_path = normalized_active
        if existing is None:
            state.managed_files.append(managed_state)
        else:
            index = state.managed_files.index(existing)
            state.managed_files[index] = managed_state
        state.updated_at = now_local_iso()
        store.save_state(state)
        key = natural_key("version_prepare", state.task_id, normalized_active, payload.idempotency_key or "")
        record_result(store, key=key, tool="version_prepare", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="version_prepare", data=data)


def version_sync_tool(config: SupervisorConfig, payload: VersionSyncInput) -> ResponseEnvelope:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("version_sync", "STATE_NOT_FOUND", "No active state found for this workspace.")

    with _lock_for(store, state, "version_sync", config):
        active_path = normalize_repo_path(repo_root, payload.active_path, write=False)
        version_path = normalize_repo_path(repo_root, payload.version_path, write=False)
        normalized_active = active_path.relative_to(repo_root).as_posix()
        existing = next((item for item in state.managed_files if item.active_path == normalized_active), None)
        if existing is None:
            return _error("version_sync", "MANAGED_FILE_NOT_DECLARED", "Managed file state was not prepared.", path=normalized_active)
        data, managed_state = sync_version(active_path, version_path, existing_state=existing)
        managed_state.active_path = normalized_active
        index = state.managed_files.index(existing)
        state.managed_files[index] = managed_state
        if config.strict_mode.enabled and config.strict_mode.write_policy == "supervisor_only":
            _record_strict_write(
                state,
                path=normalized_active,
                tool="version_sync",
                expected_hash=sha256_file(active_path),
            )
        state.updated_at = now_local_iso()
        store.save_state(state)
        key = natural_key("version_sync", state.task_id, normalized_active, str(version_path), payload.idempotency_key or "")
        record_result(store, key=key, tool="version_sync", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="version_sync", data=data)


def harness_write_version_tool(config: SupervisorConfig, payload: HarnessWriteVersionInput) -> ResponseEnvelope:
    strict_error = _require_write_through_mode(
        config,
        tool="harness_write_version",
        allow_managed_write=True,
    )
    if strict_error is not None:
        return strict_error

    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("harness_write_version", "STATE_NOT_FOUND", "No active state found for this workspace.")

    with _lock_for(store, state, "harness_write_version", config):
        if state.plan.plan_revision <= 0:
            return _error("harness_write_version", "WRITE_BEFORE_PLAN", "Strict write tools require an approved plan.")

        try:
            active_path = normalize_repo_path(repo_root, payload.active_path, write=True)
            version_path = normalize_repo_path(repo_root, payload.version_path, write=True)
        except PathPolicyError as exc:
            return _error("harness_write_version", "PATH_POLICY_ERROR", str(exc))

        normalized_active = active_path.relative_to(repo_root).as_posix()
        normalized_version = version_path.relative_to(repo_root).as_posix()
        if not _is_allowed_path(normalized_active, state.plan.allowed_files):
            return _error(
                "harness_write_version",
                "PATCH_OUTSIDE_ALLOWED_FILES",
                "Managed write target is not listed in allowed_files.",
                path=normalized_active,
            )

        managed_state = next((item for item in state.managed_files if item.active_path == normalized_active), None)
        if managed_state is None:
            return _error(
                "harness_write_version",
                "MANAGED_FILE_NOT_DECLARED",
                "Managed file state was not prepared for strict mode writing.",
                path=normalized_active,
            )
        if managed_state.pending_version is None:
            return _error(
                "harness_write_version",
                "NO_PENDING_VERSION",
                "Managed file has no pending version to edit.",
                path=normalized_active,
            )

        pending_path = Path(managed_state.pending_version)
        if pending_path.resolve() != version_path.resolve():
            return _error(
                "harness_write_version",
                "VERSION_PATH_MISMATCH",
                "Version path must match the currently pending version snapshot.",
                path=normalized_version,
            )

        bytes_written = write_version_text(version_path, payload.content, encoding=payload.encoding)
        state.updated_at = now_local_iso()
        store.save_state(state)
        data = HarnessWriteVersionData(
            active_path=normalized_active,
            version_path=normalized_version,
            bytes_written=bytes_written,
            instruction="Call version_sync to copy the pending version into the active mirror.",
        )
        key = natural_key(
            "harness_write_version",
            state.task_id,
            normalized_active,
            normalized_version,
            payload.idempotency_key or "",
        )
        record_result(store, key=key, tool="harness_write_version", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="harness_write_version", data=data)


def harness_apply_patch_tool(config: SupervisorConfig, payload: HarnessApplyPatchInput) -> ResponseEnvelope:
    strict_error = _require_write_through_mode(
        config,
        tool="harness_apply_patch",
        allow_apply_patch=True,
    )
    if strict_error is not None:
        return strict_error

    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("harness_apply_patch", "STATE_NOT_FOUND", "No active state found for this workspace.")

    with _lock_for(store, state, "harness_apply_patch", config):
        if state.plan.plan_revision <= 0:
            return _error("harness_apply_patch", "WRITE_BEFORE_PLAN", "Strict write tools require an approved plan.")

        try:
            target_path = normalize_repo_path(repo_root, payload.target_path, write=True)
        except PathPolicyError as exc:
            return _error("harness_apply_patch", "PATH_POLICY_ERROR", str(exc))

        normalized_target = target_path.relative_to(repo_root).as_posix()
        if not _is_allowed_path(normalized_target, state.plan.allowed_files):
            return _error(
                "harness_apply_patch",
                "PATCH_OUTSIDE_ALLOWED_FILES",
                "Patch target is not listed in allowed_files.",
                path=normalized_target,
            )
        if normalized_target in {decl.active_path for decl in state.plan.managed_files}:
            return _error(
                "harness_apply_patch",
                "MANAGED_FILE_WRITE_REQUIRES_VERSION_FLOW",
                "Managed files must use version_prepare -> harness_write_version -> version_sync.",
                path=normalized_target,
            )

        exists = target_path.exists()
        if exists:
            current_text = target_path.read_text(encoding=payload.encoding)
            if payload.expected_current_text is not None and current_text != payload.expected_current_text:
                return _error(
                    "harness_apply_patch",
                    "PATCH_PRECONDITION_FAILED",
                    "Current file contents do not match expected_current_text.",
                    path=normalized_target,
                )
        else:
            if not payload.create_if_missing:
                return _error(
                    "harness_apply_patch",
                    "PATCH_TARGET_MISSING",
                    "Patch target does not exist and create_if_missing=false.",
                    path=normalized_target,
                )
            current_text = ""

        changed = current_text != payload.updated_text
        if changed or not exists:
            atomic_write_text(target_path, payload.updated_text, encoding=payload.encoding)
        _record_strict_write(
            state,
            path=normalized_target,
            tool="harness_apply_patch",
            expected_hash=sha256_file(target_path),
        )
        state.updated_at = now_local_iso()
        store.save_state(state)
        data = HarnessApplyPatchData(
            path=normalized_target,
            bytes_written=len(payload.updated_text.encode(payload.encoding)),
            changed=changed,
            created=not exists,
        )
        key = natural_key("harness_apply_patch", state.task_id, normalized_target, payload.idempotency_key or "")
        record_result(store, key=key, tool="harness_apply_patch", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="harness_apply_patch", data=data)


def lesson_capture_tool(config: SupervisorConfig, payload: LessonCaptureInput) -> ResponseEnvelope:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("lesson_capture", "STATE_NOT_FOUND", "No active state found for this workspace.")
    try:
        ensure_safe_text(payload.correction, payload.mistake_pattern, payload.prevention_rule)
    except SecretScanError:
        return _error("lesson_capture", "SECRET_DETECTED", "Sensitive or raw content cannot be persisted.")

    with _lock_for(store, state, "lesson_capture", config):
        lessons_path = append_lesson(store.tasks_dir, payload.prevention_rule)
        if config.hermes.mode == "outbox":
            outbox_path, _ = write_outbox_note(
                config.hermes_outbox_root,
                note_type="lesson",
                task_id=state.task_id,
                project_id=state.project_id,
                workspace_id=state.workspace_id,
                repo_root=state.repo_root,
                scope="project",
                memory_kind="lesson",
                source_tool="lesson_capture",
                title="Lesson",
                compact_summary=payload.prevention_rule,
            )
            hermes_path = str(outbox_path)
        else:
            try:
                hermes_path = append_direct_memory(config, payload.prevention_rule, target="memory").path
            except HermesDirectModeError as exc:
                return _error("lesson_capture", exc.code, exc.message)
        data = LessonCaptureData(lesson_recorded=True, lessons_path=str(lessons_path), hermes_outbox_path=hermes_path)
        key = natural_key("lesson_capture", state.project_id, payload.correction, payload.prevention_rule, payload.idempotency_key or "")
        record_result(store, key=key, tool="lesson_capture", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="lesson_capture", data=data)


def wiki_note_tool(config: SupervisorConfig, note: WikiNoteInput, *, project_id: str) -> ResponseEnvelope:
    try:
        ensure_safe_text(note.summary, *note.evidence, *note.next_steps)
    except SecretScanError:
        return _error("wiki_note", "SECRET_DETECTED", "Sensitive or raw content cannot be persisted.")
    data = write_wiki_note(config.obsidian_root, config.obsidian.wiki_root, project_id, note, obsidian_config=config.obsidian)
    return ResponseEnvelope.success(tool="wiki_note", data=data)


def harness_finish_tool(config: SupervisorConfig, payload: HarnessFinishInput) -> ResponseEnvelope:
    repo_root = Path(payload.repo_root).resolve()
    identity = build_identity(repo_root)
    store = _workspace_store(config, identity.workspace_id)
    try:
        state = store.load_state()
    except StateStoreError:
        return _error("harness_finish", "STATE_NOT_FOUND", "No active state found for this workspace.")

    if state.phase == "FINISHED":
        worklog_path = str(store.tasks_dir / "worklog.md")
        return ResponseEnvelope.success(tool="harness_finish", data=HarnessFinishData(phase="FINISHED", worklog_path=worklog_path, state=state))

    writeback_texts: list[str] = []
    if payload.writeback is not None:
        writeback_texts.extend(payload.writeback.targets)
        writeback_texts.extend(payload.writeback.completed_targets)
        writeback_texts.extend(payload.writeback.warnings)
        if payload.writeback.blocked_reason:
            writeback_texts.append(payload.writeback.blocked_reason)
    try:
        ensure_safe_text(payload.summary, *payload.decisions, *payload.failed_attempts, *writeback_texts)
    except SecretScanError:
        return _error("harness_finish", "SECRET_DETECTED", "Sensitive or raw content cannot be persisted.")

    with _lock_for(store, state, "harness_finish", config):
        final_check = run_harness_check(
            repo_root,
            state,
            config=config,
            include_staged=config.git_guard.include_staged,
            include_untracked=config.git_guard.include_untracked,
        )
        fingerprint_before = compute_finish_fingerprint(repo_root, state)
        if not final_check.check_passed:
            return ResponseEnvelope.success(tool="harness_finish", data=final_check)

        state.verification_results = normalize_verification_results(payload.verification_results, payload.tests_run)
        verification_violations = _verification_violations(state, payload.finish_even_with_warnings)
        if any(item.severity == "error" for item in verification_violations):
            final_check.violations.extend(verification_violations)
            return ResponseEnvelope.success(tool="harness_finish", data=final_check)
        final_check.violations.extend(verification_violations)
        worklog_path = append_worklog_entry(store.tasks_dir, payload.summary)
        wiki_data = None
        if payload.create_wiki_note:
            wiki_input = WikiNoteInput(
                repo_root=payload.repo_root,
                task_id=state.task_id,
                type="task",
                title=state.task,
                summary=payload.summary,
                evidence=payload.decisions,
                next_steps=[],
            )
            wiki_data = write_wiki_note(
                config.obsidian_root,
                config.obsidian.wiki_root,
                state.project_id,
                wiki_input,
                obsidian_config=config.obsidian,
            )
            if not wiki_data.created and wiki_data.warning_code == "WIKI_NOTE_WRITE_FAILED" and payload.require_wiki_note:
                return _error("harness_finish", "WIKI_NOTE_WRITE_FAILED", wiki_data.message)
            if wiki_data.wikilink:
                state.wiki_notes.append(wiki_data.wikilink)
        writeback_required = config.memory_policy.require_finish_writeback_status or payload.require_writeback_status
        if payload.writeback is not None:
            finish_writeback = FinishWritebackState(
                required=writeback_required,
                status=payload.writeback.status,
                targets=payload.writeback.targets,
                completed_targets=payload.writeback.completed_targets,
                blocked_reason=payload.writeback.blocked_reason,
                warnings=payload.writeback.warnings,
                recorded_at=now_local_iso(),
            )
        elif wiki_data and wiki_data.wikilink:
            finish_writeback = FinishWritebackState(
                required=writeback_required,
                status="completed",
                targets=[wiki_data.wikilink],
                completed_targets=[wiki_data.wikilink],
                recorded_at=now_local_iso(),
            )
        else:
            finish_writeback = FinishWritebackState(required=writeback_required, recorded_at=now_local_iso())
        if writeback_required and payload.writeback is None and not (wiki_data and wiki_data.wikilink):
            return _error(
                "harness_finish",
                "WRITEBACK_STATUS_REQUIRED",
                "writeback status is required before finish in the active memory profile.",
            )
        if writeback_required and finish_writeback.status in {"deferred", "blocked"}:
            return _error(
                "harness_finish",
                "WRITEBACK_NOT_COMPLETED",
                "writeback status must be completed or not_required before finish.",
            )
        if finish_writeback.status == "completed":
            missing_targets = [target for target in finish_writeback.targets if target not in finish_writeback.completed_targets]
            if missing_targets:
                return _error(
                    "harness_finish",
                    "WRITEBACK_TARGETS_INCOMPLETE",
                    "writeback status is completed but not all targets are listed as completed.",
                )
        state.finish_writeback = finish_writeback
        compact_summary = payload.summary[: config.memory_policy.max_hermes_summary_chars]
        if wiki_data and wiki_data.wikilink:
            compact_summary = f"{compact_summary}\n\n{wiki_data.wikilink}"
        if config.hermes.mode == "outbox":
            handoff_path, _ = write_outbox_note(
                config.hermes_outbox_root,
                note_type="handoff",
                task_id=state.task_id,
                project_id=state.project_id,
                workspace_id=state.workspace_id,
                repo_root=state.repo_root,
                scope="project",
                memory_kind="handoff",
                source_tool="harness_finish",
                title="Handoff",
                compact_summary=compact_summary,
            )
            handoff = {
                "mode": "outbox",
                "requested_mode": "outbox",
                "actual_mode": "outbox",
                "path": str(handoff_path),
                "fallback": False,
                "fallback_reason": None,
            }
        else:
            try:
                direct_result = append_direct_memory(config, compact_summary, target="memory")
            except HermesDirectModeError as exc:
                return _error("harness_finish", exc.code, exc.message)
            handoff_path = Path(direct_result.path)
            handoff = {
                "mode": direct_result.actual_mode,
                "requested_mode": direct_result.requested_mode,
                "actual_mode": direct_result.actual_mode,
                "path": direct_result.path,
                "fallback": direct_result.fallback,
                "fallback_reason": direct_result.fallback_reason,
            }

        fingerprint_after = compute_finish_fingerprint(repo_root, state)
        if fingerprint_before != fingerprint_after:
            final_check.violations.append(
                ViolationItem(
                    type="worktree_changed_during_finish",
                    message="Worktree changed between final check and finish write.",
                    severity="error",
                    path=None,
                )
            )
            return ResponseEnvelope.success(tool="harness_finish", data=final_check)

        state.phase = "FINISHED"
        state.handoff_written = True
        state.updated_at = now_local_iso()
        store.save_state(state)
        data = HarnessFinishData(
            phase="FINISHED",
            worklog_path=str(worklog_path),
            handoff=handoff,
            wiki_note=wiki_data,
            writeback=finish_writeback,
            state=state,
        )
        key = natural_key("harness_finish", state.task_id, payload.idempotency_key or "")
        record_result(store, key=key, tool="harness_finish", task_id=state.task_id, payload=payload.model_dump(), result=data.model_dump())
        return ResponseEnvelope.success(tool="harness_finish", data=data)
