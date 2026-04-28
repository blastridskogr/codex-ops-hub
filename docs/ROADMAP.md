# Roadmap

## Implemented

- Supervisor CLI and MCP server.
- Global install and project install.
- Role agent templates.
- AGENTS and Skills installation.
- Task lifecycle tools.
- Git guard.
- Managed file versioning.
- Hermes-style outbox handoffs.
- Obsidian/QMD/vector memory lookup paths.
- Current-project/global evidence filtering in implemented lookup paths.
- Bounded `memory_lookup` diagnostics with lookup timing and deadline warnings.
- Adaptive `memory_preflight` CLI/MCP bridge for read-before-work decisions.
- Optional `harness_plan` preflight gate through
  `memory_policy.require_memory_preflight_for_plan` or per-plan
  `require_memory_preflight`.
- Optional `harness_finish` writeback status gate through
  `memory_policy.require_finish_writeback_status` or per-finish
  `require_writeback_status`.
- Writeback queue automation through `harness_checkpoint` `writeback_items` and
  `harness_finish` `writeback_queue_updates` for durable decisions, bugs,
  workflows, task logs, handoffs, status changes, and source/provenance updates.
- Source intake Phase 0 schema baseline: `SourceManifest`, `SourceNote`, and
  `ImportedLesson` frontmatter.
- Source intake Phase 3A CLI skeleton:
  `source-status`, `source-ingest`, `source-review`, and `source-compile`
  dry-run planning for local repo files.
- `source-promote` for reviewed source notes with operator-supplied summaries.

## Next

- Lightweight source compile apply for reviewed repo text, Markdown, reviewed
  logs, and reviewed conversations.
- Cross-project candidate/import/promote workflow.
- Source governance doctor.
- Synthetic cross-project validation.

## Later

- Heavy source ingest for PDF, DOCX, XLSX, PPTX, images, web pages, and archives.
- AppServer-supervised controller track.
- Project-local `docs/wiki` portability track.
