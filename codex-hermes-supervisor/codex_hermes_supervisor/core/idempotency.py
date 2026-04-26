"""Idempotency helpers and result store integration."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from codex_hermes_supervisor.schemas.state import IdempotencyRecord

from .locks import now_local_iso
from .state_store import WorkspaceStateStore


class IdempotencyMismatchError(RuntimeError):
    """Raised when an idempotency key is reused with a different payload."""


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_hash(payload: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_stable_json(payload).encode('utf-8')).hexdigest()}"


def natural_key(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def get_record(store: WorkspaceStateStore, key: str) -> IdempotencyRecord | None:
    state = store.load_idempotency()
    return next((record for record in state.records if record.key == key), None)


def validate_existing(store: WorkspaceStateStore, key: str, payload: dict[str, Any]) -> IdempotencyRecord | None:
    record = get_record(store, key)
    if record is None:
        return None
    current_hash = payload_hash(payload)
    if record.payload_hash != current_hash:
        raise IdempotencyMismatchError("IDEMPOTENCY_PAYLOAD_MISMATCH")
    return record


def record_result(
    store: WorkspaceStateStore,
    *,
    key: str,
    tool: str,
    task_id: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> IdempotencyRecord:
    state = store.load_idempotency()
    record = IdempotencyRecord(
        key=key,
        tool=tool,
        task_id=task_id,
        created_at=now_local_iso(),
        payload_hash=payload_hash(payload),
        result=result,
    )
    state.records = [existing for existing in state.records if existing.key != key]
    state.records.append(record)
    store.save_idempotency(state)
    return record
