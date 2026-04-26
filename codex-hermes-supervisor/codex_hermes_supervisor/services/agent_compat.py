"""Codex custom-agent compatibility checks and repair helpers."""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core import paths
from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.identity import normalize_windows_path

_MANAGED_AGENT_MARKER = '# managed_id = "codex-hermes-harness"'
_REQUIRED_AGENT_FIELDS = ("name", "description", "developer_instructions")


class AgentCompatCheck(BaseModel):
    name: str
    status: str
    details: str = ""


class AgentCompatReport(BaseModel):
    checks: list[AgentCompatCheck] = Field(default_factory=list)
    agent_root: str
    codex_command_available: bool
    codex_state_db_path: str
    notes: list[str] = Field(default_factory=list)


class AgentRepairAction(BaseModel):
    path: str
    action: str
    details: str = ""


class AgentRepairReport(BaseModel):
    dry_run: bool
    agent_root: str
    actions: list[AgentRepairAction] = Field(default_factory=list)
    backups: list[str] = Field(default_factory=list)


class AgentRuleFinding(BaseModel):
    path: str
    severity: str
    code: str
    message: str


class AgentRuleLintReport(BaseModel):
    repo_root: str
    checked_files: list[str] = Field(default_factory=list)
    findings: list[AgentRuleFinding] = Field(default_factory=list)
    ok: bool = True


@dataclass(frozen=True)
class AgentValidationResult:
    valid: bool
    errors: list[str]


def validate_agent_toml_content(content: str) -> AgentValidationResult:
    """Validate the minimum standalone custom-agent fields required by Codex."""

    try:
        payload = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        return AgentValidationResult(valid=False, errors=[f"failed to parse TOML: {exc}"])

    errors: list[str] = []
    if not isinstance(payload, dict):
        return AgentValidationResult(valid=False, errors=["agent role file must contain a TOML table"])
    for field in _REQUIRED_AGENT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing required field: {field}")
    return AgentValidationResult(valid=not errors, errors=errors)


def validate_generated_agent_templates(templates: dict[str, str]) -> None:
    """Fail fast if any generated managed-agent template drifts from Codex's schema."""

    failures: list[str] = []
    for filename, content in templates.items():
        result = validate_agent_toml_content(content)
        if not result.valid:
            failures.append(f"{filename}: {', '.join(result.errors)}")
    if failures:
        raise ValueError("managed agent templates are invalid: " + "; ".join(failures))


def build_codex_compat_report(*, user_home: Path | None = None, templates: dict[str, str]) -> AgentCompatReport:
    """Inspect Codex-facing custom-agent compatibility without mutating Codex internal DB state."""

    home = user_home or paths.user_home()
    agent_root = home / ".codex" / "agents"
    codex_state_db = home / ".codex" / "state_5.sqlite"
    codex_command_available = shutil.which("codex") is not None
    report = AgentCompatReport(
        agent_root=normalize_windows_path(agent_root),
        codex_command_available=codex_command_available,
        codex_state_db_path=normalize_windows_path(codex_state_db),
        notes=["Codex internal SQLite state is not managed or mutated by this harness."],
    )
    report.checks.append(
        AgentCompatCheck(
            name="codex_command",
            status="ok" if codex_command_available else "warning",
            details="available" if codex_command_available else "codex command not found on PATH",
        )
    )
    report.checks.append(
        AgentCompatCheck(
            name="agent_root",
            status="ok" if agent_root.exists() else "warning",
            details="exists" if agent_root.exists() else "missing",
        )
    )
    report.checks.append(
        AgentCompatCheck(
            name="codex_state_db",
            status="info",
            details=f"diagnostic only: {normalize_windows_path(codex_state_db)}",
        )
    )

    for filename, template in templates.items():
        template_check = validate_agent_toml_content(template)
        report.checks.append(
            AgentCompatCheck(
                name=f"template:{filename}",
                status="ok" if template_check.valid else "error",
                details="schema valid" if template_check.valid else "; ".join(template_check.errors),
            )
        )
        path = agent_root / filename
        if not path.exists():
            report.checks.append(
                AgentCompatCheck(
                    name=f"agent:{filename}",
                    status="warning",
                    details="managed agent file missing",
                )
            )
            continue
        content = path.read_text(encoding="utf-8")
        if _MANAGED_AGENT_MARKER not in content:
            report.checks.append(
                AgentCompatCheck(
                    name=f"agent:{filename}",
                    status="warning",
                    details="exists but is not harness-managed; skipped automatic schema repair",
                )
            )
            continue
        result = validate_agent_toml_content(content)
        report.checks.append(
            AgentCompatCheck(
                name=f"agent:{filename}",
                status="ok" if result.valid else "error",
                details="schema valid" if result.valid else "; ".join(result.errors),
            )
        )
    return report


def repair_managed_agents(
    *,
    templates: dict[str, str],
    user_home: Path | None = None,
    dry_run: bool = True,
) -> AgentRepairReport:
    """Repair harness-managed agent TOML files to match the current schema template."""

    validate_generated_agent_templates(templates)
    home = user_home or paths.user_home()
    agent_root = home / ".codex" / "agents"
    report = AgentRepairReport(dry_run=dry_run, agent_root=normalize_windows_path(agent_root))
    agent_root.mkdir(parents=True, exist_ok=True)

    for filename, template in templates.items():
        path = agent_root / filename
        normalized = normalize_windows_path(path)
        if not path.exists():
            if dry_run:
                report.actions.append(AgentRepairAction(path=normalized, action="would_create", details="managed agent file missing"))
            else:
                atomic_write_text(path, template)
                report.actions.append(AgentRepairAction(path=normalized, action="create", details="created managed agent file from current template"))
            continue

        current = path.read_text(encoding="utf-8")
        if _MANAGED_AGENT_MARKER not in current:
            report.actions.append(AgentRepairAction(path=normalized, action="skip", details="not managed by Codex-Hermes harness"))
            continue

        result = validate_agent_toml_content(current)
        if result.valid:
            report.actions.append(AgentRepairAction(path=normalized, action="ok", details="already compatible"))
            continue

        backup = path.with_name(path.name + ".bak")
        if dry_run:
            report.actions.append(AgentRepairAction(path=normalized, action="would_update", details="; ".join(result.errors)))
            report.backups.append(normalize_windows_path(backup))
            continue

        atomic_write_text(backup, current)
        report.backups.append(normalize_windows_path(backup))
        atomic_write_text(path, template)
        report.actions.append(AgentRepairAction(path=normalized, action="update", details="rewrote managed agent file from current template"))

    return report


_WEAKENING_PATTERNS: list[tuple[str, str]] = [
    ("HARNESS_FINISH_BYPASS", "harness_finish"),
    ("SILENT_FALLBACK_ALLOWED", "silent fallback"),
    ("DESTRUCTIVE_CLEANUP_ALLOWED", "automatic destructive cleanup"),
    ("CROSS_PROJECT_MEMORY_AS_EVIDENCE", "cross-project"),
    ("QMD_VECTOR_AS_TRUTH", "qmd/vector"),
    ("IGNORE_GLOBAL_AGENTS", "global agents"),
]


def _instruction_files(repo_root: Path) -> list[Path]:
    candidates = [
        paths.codex_root() / "AGENTS.md",
        paths.codex_root() / "AGENTS.override.md",
        repo_root / "AGENTS.md",
        repo_root / "AGENTS.override.md",
    ]
    candidates.extend(repo_root.glob("**/AGENTS.md"))
    candidates.extend(repo_root.glob("**/AGENTS.override.md"))
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.exists() and candidate.is_file():
            unique.append(candidate)
    return unique


def lint_agent_instruction_rules(repo_root: Path) -> AgentRuleLintReport:
    """Detect project AGENTS guidance that weakens global Codex-Hermes invariants."""

    repo_root = repo_root.resolve()
    report = AgentRuleLintReport(repo_root=normalize_windows_path(repo_root))
    files = _instruction_files(repo_root)
    if not files:
        report.findings.append(
            AgentRuleFinding(
                path=normalize_windows_path(repo_root / "AGENTS.md"),
                severity="warning",
                code="PROJECT_AGENTS_MISSING",
                message="No project AGENTS.md was found; project-specific memory scope and verification expectations are not documented.",
            )
        )
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        report.checked_files.append(normalize_windows_path(file))
        is_project_file = repo_root in file.resolve().parents or file.resolve() == repo_root / file.name
        if is_project_file and "project_id" not in lowered and "repo_root" not in lowered:
            report.findings.append(
                AgentRuleFinding(
                    path=normalize_windows_path(file),
                    severity="warning",
                    code="PROJECT_IDENTITY_MISSING",
                    message="Project AGENTS file does not mention project_id or repo_root identity.",
                )
            )
        checks = [
            ("HARNESS_FINISH_BYPASS", ["skip harness_finish", "without harness_finish", "ignore harness_finish", "bypass harness_finish"]),
            ("SILENT_FALLBACK_ALLOWED", ["silent fallback is allowed", "silently fallback", "silently fall back", "fallback without reporting"]),
            ("DESTRUCTIVE_CLEANUP_ALLOWED", ["automatic destructive cleanup is allowed", "auto revert", "automatic revert"]),
            ("CROSS_PROJECT_MEMORY_AS_EVIDENCE", ["cross-project memory is evidence", "use other project memory as evidence"]),
            ("QMD_VECTOR_AS_TRUTH", ["qmd is source of truth", "vector is source of truth", "qmd/vector is source of truth"]),
            ("IGNORE_GLOBAL_AGENTS", ["ignore global agents", "ignore ~/.codex/agents", "ignore global rules"]),
        ]
        for code, needles in checks:
            if any(needle in lowered for needle in needles):
                report.findings.append(
                    AgentRuleFinding(
                        path=normalize_windows_path(file),
                        severity="error",
                        code=code,
                        message="Instruction weakens a non-weakenable Codex-Hermes global invariant.",
                    )
                )
    report.ok = not any(item.severity == "error" for item in report.findings)
    return report
