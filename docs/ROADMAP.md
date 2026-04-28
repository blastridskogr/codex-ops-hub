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
- Source intake Phase 0 schema baseline: `SourceManifest`, `SourceNote`, and
  `ImportedLesson` frontmatter.
- Source intake Phase 3A CLI skeleton:
  `source-status`, `source-ingest`, `source-review`, and `source-compile`
  dry-run planning for local repo files.

## Next

- Lightweight source compile apply for reviewed repo text, Markdown, reviewed
  logs, and reviewed conversations.
- `source-promote --dry-run`.
- Cross-project candidate/import/promote workflow.
- Source governance doctor.
- Synthetic cross-project validation.

## Later

- Heavy source ingest for PDF, DOCX, XLSX, PPTX, images, web pages, and archives.
- Strict active-memory hard gate that requires `memory_preflight` output in
  `harness_plan` and writeback status in `harness_finish`.
- AppServer-supervised controller track.
- Project-local `docs/wiki` portability track.
