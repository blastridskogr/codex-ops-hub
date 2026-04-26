from __future__ import annotations

import json

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.schemas.project_memory import MemorySearchHit, MemorySearchResult
from codex_hermes_supervisor.services.search_eval import evaluate_search_fixture, evaluate_search_gates


def test_search_eval_expands_backend_matrix(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "matrix",
                "defaults": {"project_id": "p1"},
                "cases": [
                    {
                        "query": "harness smoke",
                        "backends": ["qmd", "vector_local"],
                        "modes": ["keyword", "semantic"],
                        "expected_paths": ["Projects/harness-test.md"]
                    }
                ]
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
    report = evaluate_search_fixture(SupervisorConfig(), fixture)
    assert report.total_cases == 4
    assert {item.backend for item in report.backend_summaries} == {"qmd", "vector_local"}


def test_search_eval_gates_backend_mismatch(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "matrix",
                "cases": [
                    {
                        "query": "strict mode",
                        "backend": "qmd",
                        "expected_snippets": ["strict mode"]
                    }
                ]
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
    report = evaluate_search_fixture(SupervisorConfig(), fixture)
    gate = evaluate_search_gates(report, require_backend_match=True)
    assert report.backend_mismatch_cases == 1
    assert gate.ok is False
