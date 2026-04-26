# Project Onboarding

Each project should have its own Git repository and project-scoped memory.

## Recommended Layout

```text
C:\codex-projects\
  project-a\
    .git\
    AGENTS.md
    .codex\
  project-b\
    .git\
    AGENTS.md
    .codex\
```

Do not create a parent Git repository that unintentionally makes all child
projects one workspace.

## Onboard A Project

From `C:\codex-ops-hub\codex-hermes-supervisor`:

```powershell
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli project-git-ensure --repo <project-root> --dry-run
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli install-project --repo <project-root> --dry-run
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli project-memory-bootstrap --repo <project-root> --dry-run
```

Apply after review:

```powershell
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli project-git-ensure --repo <project-root>
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli install-project --repo <project-root>
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli project-memory-bootstrap --repo <project-root>
```

## Empty Project Behavior

If no project memory exists yet, create:

```text
CodexWiki/Projects/<project_id>/status.md
CodexWiki/Projects/<project_id>/overview.md
CodexWiki/Tasks/<project_id>/log.md
CodexWiki/Sources/_manifest.md
```

Do not borrow another project's memory as evidence. Other project notes are
reference-only until imported or promoted.

## Moving Project Folders

Moving a project changes absolute paths and may change workspace identity.

After moving:

1. Confirm the project still has its own Git root.
2. Re-run `project-git-ensure --dry-run`.
3. Re-run `install-project --dry-run`.
4. Add a CodexWiki migration note with old path and new path.
5. Refresh QMD/vector indexes.
6. Run `memory_lookup` from the new path.
