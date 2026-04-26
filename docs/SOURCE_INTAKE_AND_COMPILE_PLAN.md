# Source Intake And Compile Plan

Status: planned extension, not fully implemented.

This plan expands Codex Ops Hub from project memory lookup into full Official
LLM Wiki source intake and source compilation.

## Core Rule

Source ingest is not bulk storage. It is controlled conversion from raw evidence
into reviewed, linked, scoped, provenance-bearing Markdown memory.

## Pipeline

```text
raw/source artifact
-> manifest entry
-> extraction when safe
-> review when privacy/risk requires it
-> compiled Markdown wiki note
-> index/status/log/source links
-> search selector
-> runtime evidence filter
```

## Phase 0: Safety Contract

Implement before broad ingest:

- `SourceManifest` schema.
- `SourceNote` schema.
- `ImportedLesson` schema.
- `source-ingest --dry-run`.
- `source-compile --dry-run`.
- `source-review`.
- `source-promote --dry-run`.
- privacy/redaction policy.
- archive safety policy.
- no raw into Hermes.
- no raw into unrestricted wiki.
- provenance-required check.

## Source Types

Initial lightweight types:

- repo text.
- Markdown/text/config/code.
- manual notes.
- reviewed conversations.
- reviewed terminal logs.

Later heavy types:

- PDF.
- DOCX.
- XLSX/CSV.
- PPTX.
- PNG/JPG.
- external URL.
- zip/7z archives.

Heavy ingest is CLI-only until bounded and verified. MCP should expose status
and small bounded compile requests only.

## Cross-Project Import/Promote

Other-project hits:

```text
whole-vault search
-> cross_project_candidate
-> review applicability
-> import/promote
-> current-project imported note or reviewed global note
-> runtime evidence filter may allow it
```

`evidence_allowed` is runtime-computed. Do not store it as permanent
frontmatter.

## Synthetic Validation

Required validation:

1. Create dummy projects `c` and `u`.
2. Store a reusable lesson in `u`.
3. Run a `c` task without mentioning `u`.
4. Confirm `u` appears only as `cross_project_candidate`.
5. Confirm `u` cannot be used as evidence before import/promote.
6. Promote/import into `c`.
7. Confirm current-project evidence is allowed only after source read/hash.

Fail if raw secrets, customer data, unsupported archive content, or
source-read=false hits become evidence.
