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
- Source intake Phase 0 schema baseline: `SourceManifest`, `SourceNote`, and
  `ImportedLesson` frontmatter.

## Next

- Lightweight source ingest for repo text, Markdown, reviewed logs, and reviewed conversations.
- `source-ingest --dry-run`, `source-compile --dry-run`, `source-review`, and
  `source-promote --dry-run`.
- Cross-project candidate/import/promote workflow.
- Source governance doctor.
- Synthetic cross-project validation.

## Later

- Heavy source ingest for PDF, DOCX, XLSX, PPTX, images, web pages, and archives.
- Strict active-memory hard gate with `memory_preflight`.
- AppServer-supervised controller track.
- Project-local `docs/wiki` portability track.
