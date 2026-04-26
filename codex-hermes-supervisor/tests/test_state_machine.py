from __future__ import annotations

import pytest

from codex_hermes_supervisor.core.state_machine import TransitionError, can_transition, require_transition


def test_allowed_transition() -> None:
    assert can_transition("STARTED", "PLANNED") is True
    require_transition("CHECKED", "FINISHED")


def test_forbidden_transition() -> None:
    assert can_transition("STARTED", "FINISHED") is False
    with pytest.raises(TransitionError):
        require_transition("ARCHIVED", "STARTED")
