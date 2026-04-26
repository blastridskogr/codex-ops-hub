from __future__ import annotations

import json
from pathlib import Path

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.schemas.project_memory import MemorySearchResult, MemorySearchHit
from codex_hermes_supervisor.services.qmd_eval import evaluate_qmd_fixture, evaluate_qmd_gates


def test_evaluate_qmd_fixture_scores_top_hits(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "qmd-fixture",
                "cases": [
                    {
                        "query": "harness smoke",
                        "project_id": "p1",
                        "backend": "qmd",
                        "mode": "keyword",
                        "expected_paths": ["Projects/harness-test.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "codex_hermes_supervisor.services.search_eval.memory_search",
        lambda query, **kwargs: MemorySearchResult(
            query=query,
            project_id=kwargs.get("project_id"),
            mode=kwargs.get("mode", "keyword"),
            backend=kwargs.get("backend", "qmd") or "qmd",
            hits=[
                MemorySearchHit(
                    kind="note",
                    path=r"C:\Users\example\ObsidianVault\CodexWiki\Projects\harness-test.md",
                    project_id="p1",
                    score=100,
                    snippet="match",
                )
            ],
        ),
    )

    report = evaluate_qmd_fixture(SupervisorConfig(), fixture)
    assert report.total_cases == 1
    assert report.top1_hits == 1
    assert report.top3_hits == 1
    assert report.topk_hits == 1
    assert report.backend_mismatch_cases == 0
    assert report.case_results[0].top_hit.endswith(r"Projects\harness-test.md")
    assert report.case_results[0].hit_snippets == ["match"]


def test_evaluate_qmd_fixture_accepts_snippet_expectations(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "qmd-fixture",
                "cases": [
                    {
                        "query": "strict mode",
                        "project_id": "p1",
                        "backend": "qmd",
                        "mode": "keyword",
                        "expected_snippets": ["strict mode"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.search_eval.memory_search",
        lambda query, **kwargs: MemorySearchResult(
            query=query,
            project_id=kwargs.get("project_id"),
            mode=kwargs.get("mode", "keyword"),
            backend=kwargs.get("backend", "qmd") or "qmd",
            hits=[
                MemorySearchHit(
                    kind="note",
                    path=r"C:\Users\example\ObsidianVault\CodexWiki\Tasks\strict-mode.md",
                    project_id="p1",
                    score=70,
                    snippet="This note explains strict mode and supervisor-only writes.",
                )
            ],
        ),
    )
    report = evaluate_qmd_fixture(SupervisorConfig(), fixture)
    assert report.top1_hits == 1
    assert report.topk_hits == 1


def test_evaluate_qmd_fixture_tracks_forbidden_hits(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "qmd-fixture",
                "cases": [
                    {
                        "query": "strict mode",
                        "project_id": "p1",
                        "backend": "qmd",
                        "mode": "keyword",
                        "expected_paths": ["Projects/harness-test.md"],
                        "forbidden_paths": ["handoff/strict-mode.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "codex_hermes_supervisor.services.search_eval.memory_search",
        lambda query, **kwargs: MemorySearchResult(
            query=query,
            project_id=kwargs.get("project_id"),
            mode=kwargs.get("mode", "keyword"),
            backend=kwargs.get("backend", "qmd") or "qmd",
            hits=[
                MemorySearchHit(
                    kind="outbox",
                    path=r"C:\Users\example\.codex-hermes\hermes_outbox\handoff\strict-mode.md",
                    project_id="p1",
                    score=99,
                    snippet="strict mode handoff",
                )
            ],
        ),
    )

    report = evaluate_qmd_fixture(SupervisorConfig(), fixture)
    assert report.forbidden_cases == 1
    assert report.case_results[0].forbidden_hit is True
    assert report.case_results[0].forbidden_hit_paths
    assert report.case_results[0].hit_scores == [99]


def test_evaluate_qmd_gates_enforces_thresholds(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "gate-fixture",
                "cases": [
                    {
                        "query": "strict mode",
                        "project_id": "p1",
                        "backend": "qmd",
                        "mode": "keyword",
                        "expected_paths": ["Projects/harness-test.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.search_eval.memory_search",
        lambda query, **kwargs: MemorySearchResult(
            query=query,
            project_id=kwargs.get("project_id"),
            mode=kwargs.get("mode", "keyword"),
            backend=kwargs.get("backend", "qmd") or "qmd",
            hits=[],
        ),
    )
    report = evaluate_qmd_fixture(SupervisorConfig(), fixture)
    gate = evaluate_qmd_gates(report, min_top1_rate=0.9)
    assert gate.ok is False
    assert gate.failures


def test_evaluate_qmd_gates_can_require_backend_match(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "gate-fixture",
                "cases": [
                    {
                        "query": "strict mode",
                        "project_id": "p1",
                        "backend": "qmd",
                        "mode": "keyword",
                        "expected_paths": ["Projects/harness-test.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "codex_hermes_supervisor.services.search_eval.memory_search",
        lambda query, **kwargs: MemorySearchResult(
            query=query,
            project_id=kwargs.get("project_id"),
            mode=kwargs.get("mode", "keyword"),
            backend="semantic_lite",
            hits=[],
            warnings=["fallback"],
        ),
    )
    report = evaluate_qmd_fixture(SupervisorConfig(), fixture)
    gate = evaluate_qmd_gates(report, require_backend_match=True)
    assert report.backend_mismatch_cases == 1
    assert gate.ok is False
