"""Project-scoped Codex config install helpers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from codex_hermes_supervisor.core import paths
from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.identity import build_identity, normalize_windows_path

_PROJECT_TEMPLATE_ROOT_NAME = "codex-hermes-project"
_PROJECT_TEMPLATE_HEADER = "# Managed by Codex-Hermes global project template\n"
_PROJECT_MCP_SECTION = """[mcp_servers.codex_hermes_supervisor]
command = "{{SUPERVISOR_PYTHON}}"
args = [
  "-m",
  "codex_hermes_supervisor.mcp_server",
  "--profile",
  "coder"
]
enabled = true
required = false
startup_timeout_sec = 20
tool_timeout_sec = 120
"""

_PROJECT_CONFIG_TEMPLATE = _PROJECT_TEMPLATE_HEADER + """# template_source = "%USERPROFILE%\\.codex\\templates\\codex-hermes-project\\.codex\\config.toml"
# repo_root = "{{PROJECT_ROOT}}"
# project_id = "{{PROJECT_ID}}"
# workspace_id = "{{WORKSPACE_ID}}"
#
# This project-local file is a rendered copy of the global Codex-Hermes
# project template. Keep shared policy in the global template and regenerate
# project files with:
#   <repo-root>\\codex-hermes-supervisor\\.venv\\Scripts\\python.exe -m codex_hermes_supervisor.cli install-project --repo <project-root>

""" + _PROJECT_MCP_SECTION

_PROJECT_AGENTS_TEMPLATE = """<!-- Managed by Codex-Hermes global project template | template=codex-hermes-project -->
# Project Codex-Hermes Rules

This file is copied from the global project template:

`%USERPROFILE%\\.codex\\templates\\codex-hermes-project\\AGENTS.md`

Project identity:

- repo_root: `{{PROJECT_ROOT}}`
- project_id: `{{PROJECT_ID}}`
- workspace_id: `{{WORKSPACE_ID}}`

Rules:

- This project file may add narrower project-specific rules only.
- It must not weaken the global Codex-Hermes rules in `%USERPROFILE%\\.codex\\AGENTS.md`.
- Keep project facts, decisions, bugs, workflows, source notes, task handoffs, and project lessons scoped to this `project_id`.
- Do not use cross-project memory as evidence unless the source is explicitly marked `scope: global`.
- QMD/vector hits are selectors; use them as evidence only after source read/hash and `evidence_allowed=true`.
- `harness_finish` remains the formal completion gate.
- Do not enable silent fallback or automatic destructive cleanup in this project.
"""

_PROJECT_README_TEMPLATE = """# Codex-Hermes Project Template

Global template root:

`%USERPROFILE%\\.codex\\templates\\codex-hermes-project`

Copy/render into a project with:

```powershell
<repo-root>\\codex-hermes-supervisor\\.venv\\Scripts\\python.exe -m codex_hermes_supervisor.cli install-project --repo <project-root>
```

Rendered project files:

- `<project-root>\\.codex\\config.toml`
- `<project-root>\\AGENTS.md`

Shared rules stay global. Project-local files identify the project and may add
narrower rules, but they must not weaken global Codex-Hermes policy.
"""

_PROJECT_TEMPLATES: dict[Path, str] = {
    Path(".codex") / "config.toml": _PROJECT_CONFIG_TEMPLATE,
    Path("AGENTS.md"): _PROJECT_AGENTS_TEMPLATE,
    Path("README.md"): _PROJECT_README_TEMPLATE,
}


@dataclass
class ProjectInstallResult:
    config_path: Path
    agents_path: Path
    template_root: Path
    changed: bool
    changed_paths: list[Path]


def project_template_root() -> Path:
    return paths.codex_root() / "templates" / _PROJECT_TEMPLATE_ROOT_NAME


def ensure_project_templates(*, force: bool = False) -> list[Path]:
    """Ensure global project templates exist under the user Codex home."""

    root = project_template_root()
    changed: list[Path] = []
    for relative_path, content in _PROJECT_TEMPLATES.items():
        target = root / relative_path
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        should_write = existing is None or force or existing.startswith(_PROJECT_TEMPLATE_HEADER) or existing.startswith("<!-- Managed by Codex-Hermes")
        if not should_write or existing == content:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)
        changed.append(target)
    return changed


def _render_project_template(content: str, repo_root: Path) -> str:
    identity = build_identity(repo_root)
    supervisor_python = normalize_windows_path(Path(sys.executable)).replace("\\", "/")
    return (
        content.replace("{{PROJECT_ROOT}}", identity.repo_root)
        .replace("{{PROJECT_ID}}", identity.project_id)
        .replace("{{WORKSPACE_ID}}", identity.workspace_id)
        .replace("{{SUPERVISOR_PYTHON}}", supervisor_python)
    )


def _write_if_changed(path: Path, content: str, *, dry_run: bool) -> bool:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    changed = content != original
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
    return changed


def _upsert_mcp_section(text: str) -> str:
    import re

    section = _PROJECT_MCP_SECTION.rstrip() + "\n"
    section_pattern = re.compile(r"(?ms)^\[mcp_servers\.codex_hermes_supervisor\]\n.*?(?=^\[[^\n]+\]|\Z)")
    if section_pattern.search(text):
        updated = section_pattern.sub(section + "\n", text)
        return updated.rstrip() + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    if text.rstrip():
        return text.rstrip() + "\n\n" + section
    return section


def install_project_config(repo_root: Path, *, dry_run: bool = False) -> ProjectInstallResult:
    repo_root = repo_root.resolve()
    ensure_project_templates()
    root = project_template_root()

    project_config = repo_root / ".codex" / "config.toml"
    project_agents = repo_root / "AGENTS.md"
    changed_paths: list[Path] = []

    for relative_path, template_content in _PROJECT_TEMPLATES.items():
        if relative_path == Path("README.md"):
            continue
        target = repo_root / relative_path
        rendered = _render_project_template(template_content, repo_root)
        changed = _write_if_changed(target, rendered, dry_run=dry_run)
        if changed:
            changed_paths.append(target)

    return ProjectInstallResult(
        config_path=project_config,
        agents_path=project_agents,
        template_root=root,
        changed=bool(changed_paths),
        changed_paths=changed_paths,
    )


def project_config_preview(repo_root: Path) -> dict[str, object]:
    result = install_project_config(repo_root, dry_run=True)
    return {
        "config_path": normalize_windows_path(result.config_path),
        "agents_path": normalize_windows_path(result.agents_path),
        "template_root": normalize_windows_path(result.template_root),
        "would_change": result.changed,
        "would_change_paths": [normalize_windows_path(path) for path in result.changed_paths],
    }
