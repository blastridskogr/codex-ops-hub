"""MCP server skeleton.

This file intentionally avoids stdout logging. Future MCP tool binding will
arrive after the deterministic core is stabilized by tests.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from codex_hermes_supervisor.core.config import load_config
from codex_hermes_supervisor.core.identity import build_identity
from codex_hermes_supervisor.schemas.tools import (
    HarnessApplyPatchInput,
    HarnessBeginInput,
    HarnessCheckpointInput,
    HarnessCheckInput,
    HarnessFinishInput,
    HarnessPlanInput,
    HarnessWriteVersionInput,
    LessonCaptureInput,
    VersionPrepareInput,
    VersionSyncInput,
)
from codex_hermes_supervisor.schemas.wiki import WikiNoteInput
from codex_hermes_supervisor.services.harness import (
    harness_apply_patch_tool,
    harness_begin,
    harness_checkpoint_tool,
    harness_check,
    harness_finish_tool,
    harness_plan,
    harness_write_version_tool,
    lesson_capture_tool,
    version_prepare_tool,
    version_sync_tool,
    wiki_note_tool,
)
from codex_hermes_supervisor.services.project_memory import memory_lookup


def build_server(profile: str) -> FastMCP:
    config = load_config()
    _ = profile
    server = FastMCP(
        "codex-hermes-supervisor",
        instructions="Deterministic Windows harness sidecar for Codex App.",
        debug=False,
        log_level="ERROR",
    )

    @server.tool(name="harness_begin")
    def harness_begin_tool(repo_root: str, task: str, risk_hint: str | None = None, force_new_task: bool = False, idempotency_key: str | None = None):
        payload = HarnessBeginInput(
            repo_root=repo_root,
            task=task,
            risk_hint=risk_hint,
            force_new_task=force_new_task,
            idempotency_key=idempotency_key,
        )
        return harness_begin(config, payload).model_dump()

    @server.tool(name="harness_plan")
    def harness_plan_tool(
        repo_root: str,
        task_id: str,
        plan_summary: str,
        allowed_files: list[str] | None = None,
        forbidden_files: list[str] | None = None,
        managed_files: list[dict] | None = None,
        tests_required: list[str] | None = None,
        verification_steps: list[dict] | None = None,
        risk_level: str = "medium",
        delete_allowed: bool = False,
        rename_allowed: bool = False,
        allow_submodule_changes: bool = False,
        idempotency_key: str | None = None,
    ):
        payload = HarnessPlanInput(
            repo_root=repo_root,
            task_id=task_id,
            plan_summary=plan_summary,
            allowed_files=allowed_files or [],
            forbidden_files=forbidden_files or [],
            managed_files=managed_files or [],
            tests_required=tests_required or [],
            verification_steps=verification_steps or [],
            risk_level=risk_level,
            delete_allowed=delete_allowed,
            rename_allowed=rename_allowed,
            allow_submodule_changes=allow_submodule_changes,
            idempotency_key=idempotency_key,
        )
        return harness_plan(config, payload).model_dump()

    @server.tool(name="harness_check")
    def harness_check_tool(repo_root: str, task_id: str, include_staged: bool = True, include_untracked: bool = True, idempotency_key: str | None = None):
        payload = HarnessCheckInput(
            repo_root=repo_root,
            task_id=task_id,
            include_staged=include_staged,
            include_untracked=include_untracked,
            idempotency_key=idempotency_key,
        )
        return harness_check(config, payload).model_dump()

    @server.tool(name="harness_checkpoint")
    def harness_checkpoint(
        repo_root: str,
        task_id: str,
        kind: str,
        summary: str,
        evidence: list[str] | None = None,
        next_action: str | None = None,
        idempotency_key: str | None = None,
    ):
        payload = HarnessCheckpointInput(
            repo_root=repo_root,
            task_id=task_id,
            kind=kind,
            summary=summary,
            evidence=evidence or [],
            next_action=next_action,
            idempotency_key=idempotency_key,
        )
        return harness_checkpoint_tool(config, payload).model_dump()

    @server.tool(name="harness_finish")
    def harness_finish(
        repo_root: str,
        task_id: str,
        summary: str,
        tests_run: list[dict] | None = None,
        verification_results: list[dict] | None = None,
        decisions: list[str] | None = None,
        failed_attempts: list[str] | None = None,
        create_wiki_note: bool = False,
        require_wiki_note: bool = False,
        finish_even_with_warnings: bool = False,
        idempotency_key: str | None = None,
    ):
        payload = HarnessFinishInput(
            repo_root=repo_root,
            task_id=task_id,
            summary=summary,
            tests_run=tests_run or [],
            verification_results=verification_results or [],
            decisions=decisions or [],
            failed_attempts=failed_attempts or [],
            create_wiki_note=create_wiki_note,
            require_wiki_note=require_wiki_note,
            finish_even_with_warnings=finish_even_with_warnings,
            idempotency_key=idempotency_key,
        )
        return harness_finish_tool(config, payload).model_dump()

    @server.tool(name="lesson_capture")
    def lesson_capture(repo_root: str, task_id: str, correction: str, mistake_pattern: str, prevention_rule: str, idempotency_key: str | None = None):
        payload = LessonCaptureInput(
            repo_root=repo_root,
            task_id=task_id,
            correction=correction,
            mistake_pattern=mistake_pattern,
            prevention_rule=prevention_rule,
            idempotency_key=idempotency_key,
        )
        return lesson_capture_tool(config, payload).model_dump()

    @server.tool(name="version_prepare")
    def version_prepare(repo_root: str, task_id: str, active_path: str, reason: str, versioning_mode: str = "side_by_side", idempotency_key: str | None = None):
        payload = VersionPrepareInput(
            repo_root=repo_root,
            task_id=task_id,
            active_path=active_path,
            reason=reason,
            versioning_mode=versioning_mode,
            idempotency_key=idempotency_key,
        )
        return version_prepare_tool(config, payload).model_dump()

    @server.tool(name="version_sync")
    def version_sync(repo_root: str, task_id: str, active_path: str, version_path: str, idempotency_key: str | None = None):
        payload = VersionSyncInput(
            repo_root=repo_root,
            task_id=task_id,
            active_path=active_path,
            version_path=version_path,
            idempotency_key=idempotency_key,
        )
        return version_sync_tool(config, payload).model_dump()

    @server.tool(name="harness_write_version")
    def harness_write_version(
        repo_root: str,
        task_id: str,
        active_path: str,
        version_path: str,
        content: str,
        encoding: str = "utf-8",
        idempotency_key: str | None = None,
    ):
        payload = HarnessWriteVersionInput(
            repo_root=repo_root,
            task_id=task_id,
            active_path=active_path,
            version_path=version_path,
            content=content,
            encoding=encoding,
            idempotency_key=idempotency_key,
        )
        return harness_write_version_tool(config, payload).model_dump()

    @server.tool(name="harness_apply_patch")
    def harness_apply_patch(
        repo_root: str,
        task_id: str,
        target_path: str,
        updated_text: str,
        expected_current_text: str | None = None,
        create_if_missing: bool = False,
        encoding: str = "utf-8",
        idempotency_key: str | None = None,
    ):
        payload = HarnessApplyPatchInput(
            repo_root=repo_root,
            task_id=task_id,
            target_path=target_path,
            updated_text=updated_text,
            expected_current_text=expected_current_text,
            create_if_missing=create_if_missing,
            encoding=encoding,
            idempotency_key=idempotency_key,
        )
        return harness_apply_patch_tool(config, payload).model_dump()

    @server.tool(name="memory_lookup")
    def memory_lookup_tool(
        repo_root: str,
        query: str,
        project_id: str | None = None,
        workstream_id: str | None = None,
        limit: int = 8,
        mode: str = "auto",
        backend: str | None = None,
    ):
        return memory_lookup(
            query,
            repo_root=Path(repo_root),
            config=config,
            project_id=project_id,
            workstream_id=workstream_id,
            limit=limit,
            mode=mode,
            backend=backend,
        ).model_dump()

    @server.tool(name="wiki_note")
    def wiki_note(
        repo_root: str,
        task_id: str,
        type: str,
        title: str,
        summary: str,
        evidence: list[str] | None = None,
        links: list[str] | None = None,
        confidence: str = "medium",
        next_steps: list[str] | None = None,
        idempotency_key: str | None = None,
    ):
        note = WikiNoteInput(
            repo_root=repo_root,
            task_id=task_id,
            type=type,
            title=title,
            summary=summary,
            evidence=evidence or [],
            links=links or [],
            confidence=confidence,
            next_steps=next_steps or [],
            idempotency_key=idempotency_key,
        )
        identity = build_identity(Path(repo_root).resolve())
        return wiki_note_tool(config, note, project_id=identity.project_id).model_dump()

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex_hermes_supervisor.mcp_server")
    parser.add_argument("--profile", default="coder")
    args = parser.parse_args(argv)
    server = build_server(args.profile)
    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
