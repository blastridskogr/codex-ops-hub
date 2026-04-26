"""Bundled Hermes-compatible runtime for direct CLI/python adapters."""

from __future__ import annotations

import json
import sys

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.integrations.hermes import (
    HermesRecallSource,
    _excerpt_text,
    _hermes_builtin_candidates,
    append_builtin_memory,
)


def append_memory(payload: dict[str, object]) -> dict[str, object]:
    profile = str(payload.get("profile") or "coder")
    target = str(payload.get("target") or "memory")
    content = str(payload.get("content") or "")
    actual_mode = str(payload.get("requested_mode") or "python_library")
    path = append_builtin_memory(profile, content, target=target)
    return {
        "path": str(path),
        "actual_mode": actual_mode,
        "fallback_reason": None,
    }


def build_recall(payload: dict[str, object]) -> dict[str, object]:
    profile = str(payload.get("profile") or "coder")
    max_total_chars = int(payload.get("max_total_chars") or 6000)
    actual_mode = str(payload.get("requested_mode") or "python_library")
    config = SupervisorConfig()
    remaining_budget = max_total_chars
    blocks: list[str] = []
    sources: list[dict[str, object]] = []
    per_file_limits = {
        "MEMORY.md": config.hermes.builtin_recall_memory_chars,
        "USER.md": config.hermes.builtin_recall_user_chars,
        "SOUL.md": config.hermes.builtin_recall_soul_chars,
    }
    for candidate in _hermes_builtin_candidates(profile):
        if remaining_budget <= 0 or not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8").strip()
        if not text:
            continue
        excerpt_limit = min(per_file_limits.get(candidate.name, config.hermes.builtin_recall_memory_chars), remaining_budget)
        excerpt, truncated = _excerpt_text(text, excerpt_limit, config.hermes.builtin_recall_overflow_policy)
        block = f"{candidate.name}:\n{excerpt}"
        blocks.append(block)
        included = len(block)
        remaining_budget -= included + 2
        source = HermesRecallSource(
            source_type=actual_mode,
            path=str(candidate),
            included_chars=included,
            original_chars=len(text),
            truncated=truncated,
            status="used",
            overflow_policy=config.hermes.builtin_recall_overflow_policy if truncated else None,
        )
        sources.append(source.model_dump(mode="python"))
    return {
        "recall": "\n\n".join(blocks).strip(),
        "actual_mode": actual_mode,
        "fallback_reason": None,
        "sources": sources,
    }


def main() -> int:
    payload = json.load(sys.stdin)
    operation = str(payload.get("operation") or "")
    if operation == "write":
        print(json.dumps(append_memory(payload)))
        return 0
    if operation == "read":
        print(json.dumps(build_recall(payload)))
        return 0
    print(json.dumps({"error": f"Unsupported operation: {operation}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
