"""Verification schemas and normalization helpers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

VerificationKind = Literal[
    "test",
    "lint",
    "typecheck",
    "build",
    "manual_review",
    "browser_check",
    "submodule_check",
    "other",
]

VerificationStatus = Literal["passed", "failed", "skipped", "unknown"]


class VerificationStep(BaseModel):
    kind: VerificationKind
    command: str | None = None
    description: str | None = None
    required: bool = True
    reason: str = ""


class LegacyTestRun(BaseModel):
    command: str
    status: VerificationStatus
    notes: str = ""


class VerificationResult(BaseModel):
    kind: VerificationKind
    command: str | None = None
    status: VerificationStatus
    evidence: str = ""
    satisfies_step: int | None = None


def normalize_verification_results(
    verification_results: list[VerificationResult] | None,
    tests_run: list[LegacyTestRun] | None,
) -> list[VerificationResult]:
    """Normalize legacy tests into canonical verification results."""

    if verification_results:
        return verification_results
    if not tests_run:
        return []
    return [
        VerificationResult(
            kind="test",
            command=test.command,
            status=test.status,
            evidence=test.notes,
            satisfies_step=None,
        )
        for test in tests_run
    ]
