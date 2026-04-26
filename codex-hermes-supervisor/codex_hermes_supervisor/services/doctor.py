"""Doctor checks for Codex-Hermes supervisor."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from codex_hermes_supervisor.core import paths
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import normalize_windows_path
from codex_hermes_supervisor.integrations.hermes import build_runtime_report
from codex_hermes_supervisor.integrations.qmd import build_qmd_doctor_report
from codex_hermes_supervisor.services.agent_compat import build_codex_compat_report, lint_agent_instruction_rules
from codex_hermes_supervisor.services.install_global import get_managed_agent_templates


class DoctorCheck(BaseModel):
    name: str
    status: str
    details: str = ""


class DoctorReport(BaseModel):
    checks: list[DoctorCheck] = Field(default_factory=list)
    selected_skill_root: str
    selected_agent_root: str
    codex_config_path: str
    supervisor_root: str


def _command_version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return (completed.stdout or completed.stderr).strip() or None


def build_doctor_report(config: SupervisorConfig, *, codex_compat: bool = False) -> DoctorReport:
    """Inspect local environment and user-scoped install state."""

    skill_root, _ = config.discover_skill_root()
    agent_root = paths.codex_agents_root()
    codex_config = paths.codex_config_path()
    supervisor_root = paths.supervisor_root()
    report = DoctorReport(
        selected_skill_root=normalize_windows_path(skill_root),
        selected_agent_root=normalize_windows_path(agent_root),
        codex_config_path=normalize_windows_path(codex_config),
        supervisor_root=normalize_windows_path(supervisor_root),
    )

    python_version = _command_version(["py", "--version"])
    report.checks.append(
        DoctorCheck(
            name="python",
            status="ok" if python_version else "error",
            details=python_version or "py launcher not found",
        )
    )

    git_version = _command_version(["git", "--version"])
    report.checks.append(
        DoctorCheck(
            name="git",
            status="ok" if git_version else "error",
            details=git_version or "git not found",
        )
    )

    codex_exists = codex_config.exists()
    mcp_registered = False
    if codex_exists:
        text = codex_config.read_text(encoding="utf-8")
        mcp_registered = "[mcp_servers.codex_hermes_supervisor]" in text
        try:
            codex_payload = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            codex_payload = {}
        features = codex_payload.get("features", {}) if isinstance(codex_payload, dict) else {}
        memories = codex_payload.get("memories", {}) if isinstance(codex_payload, dict) else {}
        memories_enabled = bool(features.get("memories")) if isinstance(features, dict) else False
        generate_memories = bool(memories.get("generate_memories")) if isinstance(memories, dict) else False
        disable_on_external_context = bool(memories.get("disable_on_external_context")) if isinstance(memories, dict) else False
        risk = "low"
        status = "ok"
        details = "disabled"
        if memories_enabled:
            risk = "medium"
            status = "warning"
            details = "enabled as global auxiliary only; not valid project evidence"
            if generate_memories or not disable_on_external_context:
                risk = "high"
                status = "warning"
                details = "enabled with memory generation or without external-context guard; high isolation risk"
        report.checks.append(
            DoctorCheck(
                name="codex_builtin_memories",
                status=status,
                details=f"{details}; memory_isolation_risk={risk}",
            )
        )
    report.checks.append(
        DoctorCheck(
            name="codex_config",
            status="ok" if codex_exists else "error",
            details="found" if codex_exists else "missing",
        )
    )
    report.checks.append(
        DoctorCheck(
            name="mcp_registration",
            status="ok" if mcp_registered else "warning",
            details="registered in user config" if mcp_registered else "MCP server section missing from user config",
        )
    )

    report.checks.append(
        DoctorCheck(
            name="agent_root",
            status="ok" if agent_root.exists() else "warning",
            details="exists" if agent_root.exists() else "missing",
        )
    )
    report.checks.append(
        DoctorCheck(
            name="skill_root",
            status="ok" if skill_root.exists() else "warning",
            details="exists" if skill_root.exists() else "missing",
        )
    )
    report.checks.append(
        DoctorCheck(
            name="supervisor_root",
            status="ok" if supervisor_root.exists() else "warning",
            details="exists" if supervisor_root.exists() else "missing",
        )
    )

    hermes_runtime = build_runtime_report(config)
    hermes_path = shutil.which(config.hermes.cli_executable)
    report.checks.append(
        DoctorCheck(
            name="hermes",
            status="ok" if hermes_runtime.requested_mode == "outbox" or hermes_runtime.cli_available or hermes_runtime.python_module_available else "warning",
            details=", ".join(
                part
                for part in [
                    f"requested={hermes_runtime.requested_mode}",
                    f"effective={hermes_runtime.effective_mode}",
                    hermes_path or None,
                    *(hermes_runtime.warnings or []),
                ]
                if part
            ),
        )
    )

    qmd_runtime = build_qmd_doctor_report(config)
    report.checks.append(
        DoctorCheck(
            name="qmd",
            status="ok" if qmd_runtime.executable_found else "warning",
            details=", ".join(
                part
                for part in [
                    f"backend={qmd_runtime.backend_requested}",
                    qmd_runtime.version,
                    *(qmd_runtime.warnings or []),
                ]
                if part
            ),
        )
    )

    obsidian_root = config.obsidian_root
    report.checks.append(
        DoctorCheck(
            name="obsidian",
            status="ok" if (obsidian_root and obsidian_root.exists()) else "warning",
            details=normalize_windows_path(obsidian_root) if (obsidian_root and obsidian_root.exists()) else "vault not configured",
        )
    )

    if codex_compat:
        compat_report = build_codex_compat_report(templates=get_managed_agent_templates())
        report.checks.extend(DoctorCheck(name=f"codex_compat:{check.name}", status=check.status, details=check.details) for check in compat_report.checks)
        report.checks.extend(DoctorCheck(name="codex_compat:note", status="info", details=note) for note in compat_report.notes)
        cwd_lint = lint_agent_instruction_rules(Path.cwd())
        report.checks.append(
            DoctorCheck(
                name="agents_lint",
                status="ok" if cwd_lint.ok else "error",
                details=f"checked={len(cwd_lint.checked_files)} findings={len(cwd_lint.findings)}",
            )
        )

    return report
