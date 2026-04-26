"""Backend-agnostic search evaluation helpers."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.schemas.project_memory import MemorySearchHit
from codex_hermes_supervisor.services.project_memory import memory_search


class SearchEvalCase(BaseModel):
    query: str
    project_id: str | None = None
    mode: str = "keyword"
    modes: list[str] = Field(default_factory=list)
    backend: str = "qmd"
    backends: list[str] = Field(default_factory=list)
    limit: int = 5
    expected_paths: list[str] = Field(default_factory=list)
    expected_snippets: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_matrix_fields(self) -> "SearchEvalCase":
        if not self.backends:
            self.backends = [self.backend]
        if not self.modes:
            self.modes = [self.mode]
        return self


class SearchEvalDefaults(BaseModel):
    project_id: str | None = None
    limit: int | None = None
    backends: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)


class SearchEvalFixture(BaseModel):
    name: str
    defaults: SearchEvalDefaults = Field(default_factory=SearchEvalDefaults)
    cases: list[SearchEvalCase] = Field(default_factory=list)


class SearchEvalCaseResult(BaseModel):
    query: str
    backend: str
    resolved_backend: str
    mode: str
    project_id: str | None = None
    expected_paths: list[str] = Field(default_factory=list)
    expected_snippets: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    hit_count: int = 0
    top_hit: str | None = None
    top1_match: bool = False
    top3_match: bool = False
    topk_match: bool = False
    forbidden_hit: bool = False
    forbidden_hit_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hit_paths: list[str] = Field(default_factory=list)
    hit_snippets: list[str] = Field(default_factory=list)
    hit_scores: list[int] = Field(default_factory=list)


class SearchEvalBackendSummary(BaseModel):
    backend: str
    total_cases: int
    top1_hits: int
    top3_hits: int
    topk_hits: int
    forbidden_cases: int
    backend_mismatch_cases: int
    top1_rate: float
    top3_rate: float
    topk_rate: float


class SearchEvalReport(BaseModel):
    fixture_name: str
    total_cases: int
    top1_hits: int
    top3_hits: int
    topk_hits: int
    forbidden_cases: int
    backend_mismatch_cases: int
    top1_rate: float
    top3_rate: float
    topk_rate: float
    backend_summaries: list[SearchEvalBackendSummary] = Field(default_factory=list)
    case_results: list[SearchEvalCaseResult] = Field(default_factory=list)


class SearchEvalGateResult(BaseModel):
    ok: bool
    failures: list[str] = Field(default_factory=list)
    min_top1_rate: float | None = None
    min_topk_rate: float | None = None
    allow_forbidden_hits: bool = True
    require_backend_match: bool = False


def _path_matches(path: str, expected_paths: list[str]) -> bool:
    normalized = path.replace("\\", "/").lower()
    for expected in expected_paths:
        expected_norm = expected.replace("\\", "/").lower().strip()
        if expected_norm and normalized.endswith(expected_norm):
            return True
    return False


def _snippet_matches(snippet: str, expected_snippets: list[str]) -> bool:
    lowered = snippet.lower()
    for expected in expected_snippets:
        token = expected.lower().strip()
        if token and token in lowered:
            return True
    return False


def _expand_cases(fixture: SearchEvalFixture) -> list[SearchEvalCase]:
    expanded: list[SearchEvalCase] = []
    for original in fixture.cases:
        project_id = original.project_id if original.project_id is not None else fixture.defaults.project_id
        limit = original.limit if original.limit != 5 or fixture.defaults.limit is None else fixture.defaults.limit
        backends = original.backends or fixture.defaults.backends or [original.backend]
        modes = original.modes or fixture.defaults.modes or [original.mode]
        for backend in backends:
            for mode in modes:
                expanded.append(
                    SearchEvalCase.model_validate(
                        {
                            **original.model_dump(mode="python"),
                            "project_id": project_id,
                            "limit": limit,
                            "backend": backend,
                            "backends": [backend],
                            "mode": mode,
                            "modes": [mode],
                        }
                    )
                )
    return expanded


def _evaluate_case(case: SearchEvalCase, *, resolved_backend: str, hits: list[MemorySearchHit], warnings: list[str]) -> SearchEvalCaseResult:
    hit_paths = [hit.path for hit in hits]
    hit_snippets = [hit.snippet for hit in hits]
    hit_scores = [hit.score for hit in hits]
    top_hit = hit_paths[0] if hit_paths else None
    top1_match = bool(
        hits
        and (
            _path_matches(hits[0].path, case.expected_paths)
            or _snippet_matches(hits[0].snippet, case.expected_snippets)
        )
    )
    top3_match = any(
        _path_matches(hit.path, case.expected_paths) or _snippet_matches(hit.snippet, case.expected_snippets)
        for hit in hits[:3]
    ) if (case.expected_paths or case.expected_snippets) else bool(hit_paths[:3])
    topk_match = any(
        _path_matches(hit.path, case.expected_paths) or _snippet_matches(hit.snippet, case.expected_snippets)
        for hit in hits
    ) if (case.expected_paths or case.expected_snippets) else bool(hit_paths)
    forbidden_hit_paths = [path for path in hit_paths if _path_matches(path, case.forbidden_paths)]
    return SearchEvalCaseResult(
        query=case.query,
        backend=case.backend,
        resolved_backend=resolved_backend,
        mode=case.mode,
        project_id=case.project_id,
        expected_paths=case.expected_paths,
        expected_snippets=case.expected_snippets,
        forbidden_paths=case.forbidden_paths,
        hit_count=len(hit_paths),
        top_hit=top_hit,
        top1_match=top1_match,
        top3_match=top3_match,
        topk_match=topk_match,
        forbidden_hit=bool(forbidden_hit_paths),
        forbidden_hit_paths=forbidden_hit_paths,
        warnings=warnings,
        hit_paths=hit_paths,
        hit_snippets=hit_snippets,
        hit_scores=hit_scores,
    )


def _rate(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def evaluate_search_fixture(config: SupervisorConfig, fixture_path: Path) -> SearchEvalReport:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture = SearchEvalFixture.model_validate(payload)
    results: list[SearchEvalCaseResult] = []
    for case in _expand_cases(fixture):
        search = memory_search(
            case.query,
            project_id=case.project_id,
            limit=case.limit,
            mode=case.mode,
            backend=case.backend,
            config=config,
        )
        results.append(_evaluate_case(case, resolved_backend=search.backend, hits=search.hits, warnings=search.warnings))

    backend_summaries: list[SearchEvalBackendSummary] = []
    for backend in sorted({item.backend for item in results}):
        group = [item for item in results if item.backend == backend]
        backend_summaries.append(
            SearchEvalBackendSummary(
                backend=backend,
                total_cases=len(group),
                top1_hits=sum(1 for item in group if item.top1_match),
                top3_hits=sum(1 for item in group if item.top3_match),
                topk_hits=sum(1 for item in group if item.topk_match),
                forbidden_cases=sum(1 for item in group if item.forbidden_hit),
                backend_mismatch_cases=sum(1 for item in group if item.resolved_backend != item.backend),
                top1_rate=_rate(sum(1 for item in group if item.top1_match), len(group)),
                top3_rate=_rate(sum(1 for item in group if item.top3_match), len(group)),
                topk_rate=_rate(sum(1 for item in group if item.topk_match), len(group)),
            )
        )

    return SearchEvalReport(
        fixture_name=fixture.name,
        total_cases=len(results),
        top1_hits=sum(1 for item in results if item.top1_match),
        top3_hits=sum(1 for item in results if item.top3_match),
        topk_hits=sum(1 for item in results if item.topk_match),
        forbidden_cases=sum(1 for item in results if item.forbidden_hit),
        backend_mismatch_cases=sum(1 for item in results if item.resolved_backend != item.backend),
        top1_rate=_rate(sum(1 for item in results if item.top1_match), len(results)),
        top3_rate=_rate(sum(1 for item in results if item.top3_match), len(results)),
        topk_rate=_rate(sum(1 for item in results if item.topk_match), len(results)),
        backend_summaries=backend_summaries,
        case_results=results,
    )


def evaluate_search_gates(
    report: SearchEvalReport,
    *,
    min_top1_rate: float | None = None,
    min_topk_rate: float | None = None,
    allow_forbidden_hits: bool = True,
    require_backend_match: bool = False,
) -> SearchEvalGateResult:
    failures: list[str] = []
    if min_top1_rate is not None and report.top1_rate < min_top1_rate:
        failures.append(f"top1_rate {report.top1_rate:.3f} is below the required minimum {min_top1_rate:.3f}.")
    if min_topk_rate is not None and report.topk_rate < min_topk_rate:
        failures.append(f"topk_rate {report.topk_rate:.3f} is below the required minimum {min_topk_rate:.3f}.")
    if not allow_forbidden_hits and report.forbidden_cases:
        failures.append(f"{report.forbidden_cases} fixture case(s) returned forbidden hit paths.")
    if require_backend_match and report.backend_mismatch_cases:
        failures.append(f"{report.backend_mismatch_cases} fixture case(s) fell back to a backend other than the requested backend.")
    return SearchEvalGateResult(
        ok=not failures,
        failures=failures,
        min_top1_rate=min_top1_rate,
        min_topk_rate=min_topk_rate,
        allow_forbidden_hits=allow_forbidden_hits,
        require_backend_match=require_backend_match,
    )
