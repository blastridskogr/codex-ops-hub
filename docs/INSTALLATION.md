# Installation

Audience: Windows operator installing Codex Ops Hub with the official Codex App.

## Prerequisites

- Official Codex App installed and signed in.
- Git.
- Python 3.12.
- Node.js and npm if QMD retrieval is used.
- Optional: Obsidian for viewing/editing the Markdown CodexWiki vault.

## Clone

```powershell
git clone https://github.com/blastridskogr/codex-ops-hub.git C:\codex-ops-hub
cd C:\codex-ops-hub\codex-hermes-supervisor
```

## Create A Pinned Python Environment

The Python interpreter used to install the Supervisor must be the same
interpreter used by the Codex MCP command.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli --help
```

## Pre-Install Backup

Before changing global Codex files, create a restore point:

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = "$env:USERPROFILE\.codex-hermes\install_backups\$stamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

if (Test-Path "$env:USERPROFILE\.codex") {
  Copy-Item "$env:USERPROFILE\.codex" "$backupRoot\codex" -Recurse -Force
}

if (Test-Path "$env:USERPROFILE\.codex-hermes\config.yaml") {
  Copy-Item "$env:USERPROFILE\.codex-hermes\config.yaml" "$backupRoot\codex-hermes-config.yaml" -Force
}

Write-Host "Backup written to $backupRoot"
```

## Configure MCP

Use an absolute venv Python path in `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.codex_hermes_supervisor]
command = "C:/codex-ops-hub/codex-hermes-supervisor/.venv/Scripts/python.exe"
args = ["-m", "codex_hermes_supervisor.mcp_server", "--profile", "coder"]
enabled = true
required = false
startup_timeout_sec = 20
tool_timeout_sec = 120
```

Do not paste `<repo-root>` literally. Replace it with the absolute checkout path
on the target machine. Forward slashes are recommended in TOML strings.

## Install Global Rules, Skills, And Agents

Preview first:

```powershell
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli install-global --dry-run
```

Apply after review:

```powershell
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli install-global --force
```

This writes user-level Codex files such as:

- `%USERPROFILE%\.codex\config.toml`
- `%USERPROFILE%\.codex\AGENTS.md`
- `%USERPROFILE%\.codex\agents\*.toml`
- `%USERPROFILE%\.codex\skills\*`
- `%USERPROFILE%\.codex\templates\codex-hermes-project`

## Install QMD

QMD is optional but recommended for Markdown retrieval.

```powershell
npm install -g @tobilu/qmd@2.1.0
where qmd
qmd --version
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli qmd-doctor
```

Different QMD versions should be accepted only after doctor, sync, evaluation,
and memory lookup smoke checks pass.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli doctor --codex-compat
```

Expected result for this release line: tests pass and doctor reports Codex
config, MCP registration, agents, skills, Hermes mode, QMD, and Obsidian status.
