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
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli codex-session-ingest --dry-run
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli codex-session-ingest --apply
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli codex-session-compile --dry-run
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli codex-session-compile --apply
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli qmd-sync --embed --timeout-seconds 300
```

Current limits:

- Directory/bulk repo ingest is not active.
- Codex session ingest is active for `~/.codex/sessions` and
  `~/.codex/archived_sessions`; it registers transcript metadata as private
  `conversation` raw sources grouped by session `cwd`.
- Codex session ingest does not copy raw transcript text into Hermes or
  Obsidian notes.
- Codex session compile writes provenance-only Obsidian source notes under
  `Sources/[project-id]/` and conversation indexes. These notes remain
  `reference_only` until reviewed and promoted.
- Heavy PDF/DOCX/XLSX/PPTX/image/web/archive ingest is not active.
- `source-compile --apply` is active for lightweight provenance-only source
  notes. It does not summarize or copy raw private content.
- Private/customer/secret/restricted sources must be reviewed before compile.
- Large Obsidian note batches can require a longer QMD embed timeout. Use
  `qmd-sync --embed --timeout-seconds [seconds]` for batch refresh; this does
  not change the bounded `memory_lookup` budget.

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
