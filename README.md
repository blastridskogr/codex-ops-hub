# Codex Ops Hub

Codex Ops Hub is a Windows-first operations layer for the official Codex App.
It keeps the official Codex App and official Codex harness as the primary UX and
runtime, then adds an external supervisor, memory, retrieval, project setup,
verification, and handoff layer through supported extension surfaces.

Current scope:

- Official Codex App primary runtime.
- Official Codex harness and tool loop remain unmodified.
- External Supervisor MCP server.
- Global AGENTS.md, Skills, and role-agent operating rules.
- Hermes-style compact pointer and handoff memory.
- Official LLM Wiki operating model over an Obsidian Markdown vault.
- QMD/vector/Obsidian search as retrieval selectors, not sources of truth.
- Project-scoped task records, Git guard, managed file versioning, and finish gate.

This repository does not redistribute or patch the official Codex App. It also
does not publish any private Obsidian vault content, local task records, model
sessions, or user secrets.

## Why This Exists

The goal is to make Codex work reproducibly across projects:

1. Start work through a supervised task lifecycle.
2. Look up relevant project memory before planning when needed.
3. Keep long-form project memory in a compiled Markdown wiki.
4. Treat search hits as selectors until source files are read and verified.
5. Preserve generated artifacts and avoid automatic destructive cleanup.
6. Require explicit verification before declaring work complete.

## Repository Layout

```text
codex-ops-hub/
  codex-hermes-supervisor/   Python package for the Supervisor MCP and CLI
  docs/                      Public installation, operation, architecture, and attribution docs
  examples/                  Public example config snippets
  templates/                 Public project template examples
```

The Python package currently keeps the compatibility name
`codex-hermes-supervisor` / `codex_hermes_supervisor`. The public project name
is Codex Ops Hub because the system covers more than Hermes memory.

## Quick Start

Read [docs/INSTALLATION.md](docs/INSTALLATION.md) first.

Minimal Windows install outline:

```powershell
git clone https://github.com/blastridskogr/codex-ops-hub.git C:\codex-ops-hub
cd C:\codex-ops-hub\codex-hermes-supervisor
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli doctor --codex-compat
```

Then configure the official Codex App to launch the MCP server with the same
venv Python:

```toml
[mcp_servers.codex_hermes_supervisor]
command = "C:/codex-ops-hub/codex-hermes-supervisor/.venv/Scripts/python.exe"
args = ["-m", "codex_hermes_supervisor.mcp_server", "--profile", "coder"]
enabled = true
required = false
startup_timeout_sec = 20
tool_timeout_sec = 120
```

## Documentation

- [Installation](docs/INSTALLATION.md)
- [Operations](docs/OPERATIONS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Official LLM Wiki Operation](docs/OFFICIAL_LLM_WIKI.md)
- [Source Intake And Compile Plan](docs/SOURCE_INTAKE_AND_COMPILE_PLAN.md)
- [Project Onboarding](docs/PROJECT_ONBOARDING.md)
- [Security And Privacy](docs/SECURITY_AND_PRIVACY.md)
- [Attribution](docs/ATTRIBUTION.md)
- [Release Checklist](docs/RELEASE_CHECKLIST.md)
- [Roadmap](docs/ROADMAP.md)

## Current Implementation Status

Implemented:

- Supervisor CLI and MCP server.
- Task lifecycle tools: begin, plan, checkpoint, check, finish.
- Git/project identity checks.
- Global install and project install helpers.
- Official LLM Wiki adapter Skill installation.
- QMD/vector/Obsidian memory lookup paths.
- Project-scoped evidence filtering in implemented lookup paths.
- Managed file versioning support.

Planned or hardening:

- Full source-ingest/source-compile implementation.
- Heavy source ingestion for PDFs, workbooks, images, web pages, and archives.
- Strict active-memory hard gate with `memory_preflight`.
- Full source governance doctor.
- AppServer-supervised mode.

## License

Apache-2.0. See [LICENSE](LICENSE).
