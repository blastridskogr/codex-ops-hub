# Security And Privacy

Codex Ops Hub is designed to publish code, templates, and operating rules, not
private user memory.

## Do Not Publish

- `%USERPROFILE%\.codex`
- `%USERPROFILE%\.codex-hermes`
- private Obsidian vault contents.
- Hermes outbox records.
- task records and session logs.
- raw customer files.
- screenshots, PDFs, workbooks, archives.
- `.env`, tokens, credentials, API keys.
- patched proprietary app bundles.

## Public-Safe Content

- source code.
- templates.
- schemas.
- public documentation.
- public examples with placeholder paths.
- tests with synthetic data.

## Memory Privacy

Hermes-style memory is compact pointer/handoff memory. It must not store raw
logs, raw diffs, secrets, full source dumps, customer data, or prompt-injection
payloads.

Obsidian CodexWiki may store compiled project memory, but private vault content
must not be published by default.

## Source Ingest Safety

Before enabling broad source ingest:

- require dry-run.
- require review for private/customer/secret/restricted sources.
- require source refs or source hashes.
- block raw-sized compiled notes.
- quarantine unsafe material rather than deleting it.
