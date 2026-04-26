"""Compatibility wrapper around the generalized search-eval helpers."""

from __future__ import annotations

from codex_hermes_supervisor.services.search_eval import (
    SearchEvalBackendSummary as QmdEvalBackendSummary,
    SearchEvalCase as QmdEvalCase,
    SearchEvalCaseResult as QmdEvalCaseResult,
    SearchEvalFixture as QmdEvalFixture,
    SearchEvalGateResult as QmdEvalGateResult,
    SearchEvalReport as QmdEvalReport,
    evaluate_search_fixture as evaluate_qmd_fixture,
    evaluate_search_gates as evaluate_qmd_gates,
)

__all__ = [
    "QmdEvalBackendSummary",
    "QmdEvalCase",
    "QmdEvalCaseResult",
    "QmdEvalFixture",
    "QmdEvalGateResult",
    "QmdEvalReport",
    "evaluate_qmd_fixture",
    "evaluate_qmd_gates",
]
