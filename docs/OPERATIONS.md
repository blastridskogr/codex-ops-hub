# Operations

## Normal Supervised Workflow

For non-trivial work:

```text
1. harness_begin
2. memory_lookup when the task is non-trivial or memory-dependent
3. harness_plan with allowed files and verification steps
4. work
5. harness_checkpoint for decisions, failed attempts, risks, or verification
6. harness_check
7. harness_finish
```

`harness_finish` is the formal completion gate. A chat answer is not completion
unless the finish gate passes or a blocker is clearly reported.

## Memory Decision Levels

Official LLM Wiki read-before-work is adaptive. It is not whole-vault reading on
every task.

| Level | Use |
| --- | --- |
| `no_memory_needed` | Pure chat, trivial task, or no durable context dependency |
| `light_lookup` | Normal non-trivial task with low memory risk |
| `targeted_lookup` | Previous work, keyword, workstream, blocker, design/config change, multi-file risk, release/safety risk, review |
| `deep_wiki_read` | Memory architecture, release/handoff, cross-project import/promotion, major refactor, stale cleanup |

When the user mentions a keyword, previous work, or workstream, run
`memory_lookup` before planning.

`memory_lookup` has a bounded lookup budget. The default CLI/MCP budget is 55
seconds so it can return a degraded context pack before a normal MCP tool
timeout. Use `--timeout-seconds [seconds]` on the CLI for acceptance testing.
When the deadline is exceeded, the result includes `lookup_timing_ms`,
`lookup_timeout_seconds`, `lookup_deadline_exceeded`, and explicit
`MEMORY_LOOKUP_DEADLINE_EXCEEDED` warnings instead of silently hanging.

## Source Intake CLI

Official LLM Wiki source intake is dry-run-first. The current v0.2 skeleton is
local-file only and records provenance metadata before any compiled wiki write:

```powershell
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli source-status --repo "[project-root]"
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli source-ingest --repo "[project-root]" --path "README.md" --dry-run
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli source-ingest --repo "[project-root]" --path "README.md" --apply
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli source-review --repo "[project-root]" --source-id "[source-id]" --dry-run
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli source-compile --repo "[project-root]" --source-id "[source-id]" --dry-run
```

Current limits:

- Directory/bulk ingest is not active.
- Heavy PDF/DOCX/XLSX/PPTX/image/web/archive ingest is not active.
- `source-compile --apply` is intentionally blocked until compile templates,
  review gates, and governance checks are promoted.
- Private/customer/secret/restricted sources must be reviewed before compile.

## Evidence Rules

- Hermes recall is pointer memory, not same-session evidence.
- QMD/vector/Obsidian search hits are selectors only.
- A source becomes evidence only after read, hash/fingerprint capture, and
  runtime project/scope filtering.
- Cross-project hits are reference-only until imported or promoted.
- `evidence_allowed` is runtime-computed, not permanent frontmatter.

## Writeback Rules

Write durable outcomes to Obsidian CodexWiki:

- project status changes.
- decisions.
- bug root causes.
- reusable workflows.
- source/provenance updates.
- model/config/profile policy changes.
- durable user corrections when general enough.

Hermes receives only compact sanitized handoffs and pointers for next-session
recall.

## Preservation Policy

Do not perform automatic destructive cleanup. Failed attempts, stale notes, and
superseded decisions should remain available for review.

Allowed:

- mark as stale.
- mark as superseded.
- quarantine unsafe source records.
- create new corrected notes.

Not allowed by default:

- delete raw evidence.
- erase failed task artifacts.
- run destructive Git reset/checkout as cleanup.
