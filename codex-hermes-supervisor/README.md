# codex-hermes-supervisor

Compatibility package for Codex Ops Hub.

This Python package provides the Supervisor CLI and MCP server used by Codex Ops
Hub. The public repository name is `codex-ops-hub`; the package name remains
`codex-hermes-supervisor` for compatibility with the current install and MCP
configuration.

Core features:

1. task lifecycle: begin, plan, checkpoint, check, finish
2. project identity and Git guard
3. managed file versioning
4. Hermes-style compact handoff/outbox memory
5. Obsidian/QMD/vector memory lookup support
6. global AGENTS/Skills/custom agent installer
7. doctor and compatibility checks

Install:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli doctor --codex-compat
```
