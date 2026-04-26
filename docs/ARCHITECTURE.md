# Architecture

Codex Ops Hub is an external operations layer for the official Codex App.

It does not replace the official Codex App, patch Codex Desktop internals, or
fork Codex core. The official app remains the primary user interface and lower
runtime. Codex Ops Hub adds policy, state, memory, retrieval, and verification
around that runtime through supported extension surfaces.

## Current Production Model

```text
Official Codex App primary
+ official Codex harness
+ external Supervisor MCP
+ global AGENTS.md / Skills / custom agents
+ Hermes-style compact memory and project-scoped handoffs
+ Official LLM Wiki operating model
+ Obsidian CodexWiki Markdown backend
+ QMD/vector/Obsidian retrieval selectors
+ managed file versioning
+ Git guard and formal finish gate
```

## Component Roles

| Component | Role |
| --- | --- |
| Official Codex App | UX, login, model picker, thread UI, tool runtime |
| Official Codex harness | Agent loop, tool dispatch, MCP/Skills/AGENTS extension surface |
| Supervisor MCP | External task lifecycle and verification tools |
| AGENTS.md | Global and project operating rules |
| Skills | Reusable operating procedures loaded by Codex |
| Custom agents | Role profiles such as explorer, planner, implementer, verifier |
| Hermes-style memory | Compact pointer and handoff memory for next-session recall |
| Official LLM Wiki model | Source intake, compilation, indexing, linking, query, writeback, maintenance |
| Obsidian CodexWiki | Current long-form Markdown storage backend |
| QMD/vector/Obsidian search | Retrieval selectors, not sources of truth |
| Git guard | Project identity, allowed file checks, dirty worktree visibility |
| Managed file versioning | Immutable generated-file snapshots plus active mirrors |
| `harness_finish` | Formal completion gate |

## What Is Not Claimed

- No transparent proxy inside the official Desktop App.
- No forked Codex core enforcement in current production.
- No native Hermes Agent provider integration unless implemented later.
- No claim that QMD/vector hits are source evidence by themselves.
- No publication of private Obsidian vaults, raw files, user sessions, or task records.

## Current Workflow

Implemented normal flow:

```text
harness_begin
-> memory_lookup when non-trivial or memory-dependent
-> harness_plan
-> work
-> harness_checkpoint
-> harness_check
-> harness_finish
```

Planned strict active-memory flow:

```text
harness_begin
-> memory_preflight
-> harness_plan(memory_evidence)
-> work + writeback_queue
-> harness_check
-> harness_finish(writeback_status)
```

The strict active-memory flow is a planned hardening path. Do not document it as
fully enforced until implemented and verified.

## Naming

The public repository is `codex-ops-hub` because the system is broader than
Hermes memory. The Python package currently keeps the compatibility name
`codex-hermes-supervisor` / `codex_hermes_supervisor`.
