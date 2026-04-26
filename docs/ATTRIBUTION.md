# Attribution

This repository combines local implementation with concepts from public
documentation and open-source projects.

## OpenAI Codex

Used:

- Official Codex App as the primary UX/runtime.
- Official Codex harness framing.
- MCP, AGENTS.md, Skills, and custom agent extension surfaces.

Not used:

- No redistribution of the official Codex App.
- No Desktop App internal proxying.
- No forked Codex core in current production.

References:

- [OpenAI Codex harness/App Server](https://openai.com/index/unlocking-the-codex-harness/)
- OpenAI Codex product and configuration documentation.

## Supervisor / Harness Layer

Used:

- Local custom Supervisor MCP and CLI implementation.
- Agent-harness-style task lifecycle: begin, plan, checkpoint, check, finish.
- Deterministic checks around an otherwise official Codex runtime.

Not used:

- No third-party harness code is vendored in this public export.
- If a specific upstream harness is vendored later, its license and notice must
  be added before publication.

## Hermes

Used:

- Compact memory concepts.
- MEMORY/USER split concept.
- Pointer/handoff recall pattern.

Not used:

- No native Hermes Agent provider claim in current production.
- No raw long-form project memory in Hermes.

References:

- [Hermes Agent memory docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/)
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)

## Official LLM Wiki Operating Model

Used:

- Raw sources -> compiled Markdown wiki -> work/output.
- Read wiki -> do work -> write back to wiki.
- Index/current-status/log-centered operation.
- Source provenance.
- Stale/superseded maintenance.
- Obsidian-compatible Markdown.

Not used:

- No separate upstream LLM Wiki runtime is required.
- Project-local `docs/wiki` is not the current backend by default.

References:

- [LLM Wiki pattern](https://llmwiki.lol/)
- [nvk/llm-wiki](https://github.com/nvk/llm-wiki)
- [nvk LLM Wiki AGENTS protocol](https://raw.githubusercontent.com/nvk/llm-wiki/master/AGENTS.md)
- [Karpathy LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [Ss1024sS/LLM-wiki](https://github.com/Ss1024sS/LLM-wiki)
- [Ss1024sS Universal guide](https://raw.githubusercontent.com/Ss1024sS/LLM-wiki/main/UNIVERSAL.md)
- [Ss1024sS knowledge-system playbook](https://raw.githubusercontent.com/Ss1024sS/LLM-wiki/main/docs/knowledge-system-playbook.md)

## Obsidian

Used:

- Markdown vault as current CodexWiki storage backend.
- Viewer/editor/graph surface.

Not used:

- Obsidian UI state, workspace cache, sync state, and graph layout are not
  evidence.

## QMD

Used:

- Local Markdown search selector over CodexWiki.
- Pinned install line: `npm install -g @tobilu/qmd@2.1.0`.

Not used:

- QMD hits are not evidence until the referenced source file is read and
  filtered.

References:

- [tobi/qmd](https://github.com/tobi/qmd)
- npm package `@tobilu/qmd`

## Git And GitHub

Used:

- Project identity.
- Release pinning.
- `.gitignore`, license, and large-file publication policy.

References:

- [GitHub .gitignore docs](https://docs.github.com/en/get-started/getting-started-with-git/ignoring-files)
- [GitHub license docs](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-license-to-a-repository)
- [GitHub large file docs](https://docs.github.com/repositories/working-with-files/managing-large-files/about-large-files-on-github)
