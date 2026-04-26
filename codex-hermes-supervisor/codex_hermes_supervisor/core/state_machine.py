"""Persisted state transition rules."""

from __future__ import annotations

from codex_hermes_supervisor.schemas.state import Phase

_ALLOWED_TRANSITIONS: dict[Phase, set[Phase]] = {
    "IDLE": {"STARTED"},
    "STARTED": {"PLANNED"},
    "PLANNED": {"PLANNED", "CHECKED"},
    "CHECKED": {"PLANNED", "CHECKED", "FINISHED"},
    "FINISHED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


class TransitionError(RuntimeError):
    """Raised when a persisted transition violates the contract."""


def can_transition(current: Phase, nxt: Phase) -> bool:
    return nxt in _ALLOWED_TRANSITIONS[current]


def require_transition(current: Phase, nxt: Phase) -> None:
    if not can_transition(current, nxt):
        raise TransitionError(f"Transition {current} -> {nxt} is not allowed.")
