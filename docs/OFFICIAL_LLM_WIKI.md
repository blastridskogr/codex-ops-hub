# Official LLM Wiki Operation

Codex Ops Hub applies the Official LLM Wiki operating model to the current
Codex-Hermes Supervisor and Obsidian Markdown backend.

LLM Wiki is not just note storage. It is a knowledge operating loop:

```text
source intake
-> compilation into structured Markdown wiki pages
-> indexing
-> linking
-> query/use
-> writeback
-> stale/superseded maintenance
```

## Storage Backend

Current backend:

```text
%USERPROFILE%\ObsidianVault\CodexWiki
```

The source of truth is the Markdown file tree, not Obsidian app UI state,
workspace cache, or graph layout.

Expected structure:

```text
CodexWiki/
  _index.md
  current-status.md
  log.md
  _schema/
    WIKI_SCHEMA.md
  Projects/
  Workstreams/
  Tasks/
  Decisions/
  Bugs/
  Workflows/
  Sources/
    _manifest.md
```

Project-local `docs/wiki` is a future portability option, not the default
backend in this setup.

## Read-Before-Work

Read-before-work is adaptive:

- use direct entrypoints for context.
- use `memory_preflight` to record the memory decision and gather plan inputs.
- use `memory_lookup` for lower-level keyword/workstream recall diagnostics.
- use QMD/vector/Obsidian search to select pages.
- read and hash the referenced source before treating it as evidence.

Do not read the entire vault for every task.

`harness_plan` can enforce this loop when
`memory_policy.require_memory_preflight_for_plan` or the per-plan
`require_memory_preflight` flag is enabled. In that mode, planning must include
a valid `memory_preflight` result.

`harness_finish` can enforce durable writeback status when
`memory_policy.require_finish_writeback_status` or the per-finish
`require_writeback_status` flag is enabled. In that mode, completed work must
either write a wiki note or provide completed writeback targets before finish.

## Cross-Project Knowledge

Whole-vault search may find related notes from other projects.

Default:

- current project notes can become evidence after source read/hash.
- `scope: global` notes can become evidence after source read/hash.
- other project notes are `cross_project_candidate` only.

To reuse another project's knowledge, import/promote it into the current
project or review it into `scope: global`.
