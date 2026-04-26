"""User-scoped install-global apply and rollback helpers."""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from codex_hermes_supervisor.core import paths
from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.core.identity import normalize_windows_path
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.services.agent_compat import validate_generated_agent_templates
from codex_hermes_supervisor.services.install_project import ensure_project_templates

_AGENTS_HEADER = "# Managed by Codex-Hermes Official LLM Wiki Harness\n# managed_id = \"codex-hermes-harness\"\n# managed_schema_version = 1\n\n"
_AGENTS_MD_BEGIN = "<!-- BEGIN CODEX-HERMES-HARNESS -->"
_AGENTS_MD_END = "<!-- END CODEX-HERMES-HARNESS -->"
_MCP_SECTION_HEADER = "[mcp_servers.codex_hermes_supervisor]"
_MCP_SECTION_BODY = """[mcp_servers.codex_hermes_supervisor]
command = "{{SUPERVISOR_PYTHON}}"
args = [
  "-m",
  "codex_hermes_supervisor.mcp_server",
  "--profile",
  "coder"
]
enabled = true
required = false
startup_timeout_sec = 20
tool_timeout_sec = 120
"""


def _skills_marker(managed_id: str) -> str:
    return (
        "<!-- Managed by Codex-Hermes Official LLM Wiki Harness | "
        f"managed_id={managed_id} | managed_schema_version=1 -->\n\n"
    )


def _is_managed_text(text: str) -> bool:
    return "Managed by Codex-Hermes" in text and "managed_schema_version" in text

_GLOBAL_AGENTS_BLOCK = f"""{_AGENTS_MD_BEGIN}
# Global Codex-Hermes Harness

These are global operating rules for every Codex project on this machine.
Project-local `AGENTS.md` files may add narrower project rules, but they must
not weaken these global rules.

Current global production mode:

```text
Official Codex App primary
+ Official Codex harness
+ external Codex-Hermes Supervisor MCP
+ AGENTS / Skills / custom agents
+ Hermes / Obsidian / QMD / vector_local
+ managed file versioning / finish gate
```

## Supervised Workflow

- Use the Codex-Hermes supervised workflow for non-trivial work.
- Use `harness_begin`, `harness_plan`, `harness_check`, and `harness_finish` for task state, verification, worklog, and handoff.
- Do not mark work complete without verification evidence.
- Treat `harness_finish` as the formal completion gate.
- Preserve user and generated artifacts for review; do not perform automatic destructive cleanup.
- Managed or generated files use immutable version snapshots plus active mirrors.
- Production source is not managed by inference; use explicit managed-file scope.

## Memory And Retrieval

- Official LLM Wiki operating model is the memory standard: raw source intake,
  compilation into structured Markdown wiki pages, indexing, linking, query,
  writeback, and stale/superseded maintenance.
- The global `official-llm-wiki` Skill applies the official LLM
  Wiki operating model to the current Codex-Hermes and Obsidian CodexWiki stack.
- Source intake and compile expansion is governed by
  `docs/CODEX_HERMES_OFFICIAL_LLM_WIKI_SOURCE_INTAKE_AND_COMPILE_PLAN_V1.md`.
  Until implemented and verified, do not claim bulk ingest is active.
- Source ingest/compile must be CLI-first for bulk work, dry-run-first,
  review-gated for private/customer/secret/restricted material, and
  provenance-required.
- Cross-project source or memory hits are reference-only until imported or
  promoted into the current project or reviewed as `scope: global`.
- `evidence_allowed` is a runtime evidence-filter result, not permanent note
  frontmatter.
- Obsidian CodexWiki is the current Markdown storage backend for LLM Wiki
  notes on this machine.
- Hermes stores compact durable memory and pointers. `MEMORY.md` is for
  agent/project facts, conventions, tool quirks, reusable lessons, and pointers;
  `USER.md` is for user preferences, communication style, durable corrections,
  and things to avoid.
- QMD and vector_local are retrieval selectors over memory artifacts, not
  sources of truth.
- Codex built-in Memories are disabled in the global default profile because
  they bypass Supervisor project evidence filters. If a local power-user profile
  enables them, treat them as global auxiliary recall only, never as project
  evidence.
- For non-trivial tasks, consult Hermes recall and relevant
  Obsidian/QMD/vector memory before planning when available.
- For non-trivial tasks with durable context, follow the Official LLM Wiki loop:
  read relevant wiki memory, work from cited sources, then write durable
  conclusions, decisions, bug causes, workflows, and source/provenance updates
  back to Obsidian CodexWiki.
- Official LLM Wiki read-before-work is adaptive, not whole-vault reading on
  every task. Before planning, choose and record one memory decision:
  `no_memory_needed`, `light_lookup`, `targeted_lookup`, or `deep_wiki_read`.
- `targeted_lookup` is required when the user mentions previous work, a keyword,
  a workstream, continuing a task, a blocker/repeated failure, operating-policy
  or design/config changes, multi-file risk, release/safety risk, or explicit
  review/verification.
- `deep_wiki_read` is required for Official LLM Wiki / Hermes / Supervisor
  memory design, release or handoff work, cross-project import/promotion,
  architecture replacement, major refactor, or stale/superseded cleanup.
- Valid no-lookup skip reasons are `pure_chat`, `trivial_task`,
  `no_durable_outcome`, or `missing_project_memory_bootstrap` only when the
  bootstrap is planned or checkpointed.
- When the user references previous work, a keyword, or a workstream name, call
  Supervisor `memory_lookup` before planning. Use the returned context pack to
  identify the current `project_id`, candidate workstream, source paths, and
  rejected reference-only hits.
- `memory_lookup` is the current implemented LLM Wiki lookup bridge. It searches
  Obsidian/QMD/vector artifacts from user keywords, reads and hashes referenced
  Markdown sources, filters evidence to the current project or `scope: global`,
  and returns a context pack for `harness_plan`.
- Hermes writeback during a task is for next-session recall, not same-session
  evidence. Current-task evidence must come from Supervisor state, Obsidian
  CodexWiki source reads, task records, and writeback queue.
- Future strict active-memory profile requires `memory_preflight` before
  `harness_plan`, `memory_evidence` in `harness_plan`, and writeback status in
  `harness_finish`. Until those tools are implemented, do not claim that this
  gate is already active.

## Project-Scoped Memory Bootstrap

- Global `AGENTS.md`, Skills, MCP bindings, model policy, role-agent policy,
  and harness workflow rules are shared operating rules.
- Project-local Codex-Hermes files must be generated from the global project
  template root:
  `%USERPROFILE%\\.codex\\templates\\codex-hermes-project`.
- The project template provides copy/render sources for `<project-root>\\AGENTS.md`
  and `<project-root>\\.codex\\config.toml`.
- Use the configured Supervisor Python to render them, normally:
  `<repo-root>\\codex-hermes-supervisor\\.venv\\Scripts\\python.exe -m codex_hermes_supervisor.cli install-project --repo <project-root>`.
- `py -m ...` is local convenience only when the package is intentionally
  installed into that Python.
- Treat global templates as the source of truth for shared project-local
  configuration. Project-local copies may identify the project and add narrower
  rules, but must not become independent policy forks.
- Memory content is not shared by default. Project facts, decisions, bugs,
  workflows, source notes, task handoffs, and project lessons must be scoped to
  the current `project_id`, `workspace_id`, and repo root.
- `USER.md` is for global user preferences and durable user corrections only.
  Do not put project-specific facts in `USER.md`.
- `MEMORY.md` may hold compact global agent facts and pointers. Project-specific
  facts belong in Supervisor project state, Obsidian CodexWiki project/task
  notes, and project-scoped Hermes/outbox records.
- At the start of non-trivial supervised work, identify the project via
  `harness_begin` and ensure the project has Git identity and Supervisor state.
- If the project's required memory/wiki entrypoints are missing, create safe
  project-scoped bootstrap notes through Supervisor/Obsidian tools when
  available. If creation is not available, record the missing entrypoints with
  `harness_checkpoint` and report the exact bootstrap action needed.
- Do not treat recalled Hermes handoffs, lessons, or Obsidian notes from other
  projects as evidence unless they are explicitly marked `scope: global`.
- QMD/vector/search hits are selectors only. A hit becomes usable evidence only
  after the referenced source file is read, hashed, and runtime evidence
  filtering allows it for the current `project_id` or `scope: global`.
- Project-scoped memory records should include `scope`, `project_id`,
  `workspace_id`, `repo_root`, and `memory_kind` metadata when the storage
  format supports it.

## Model, Agent, Skill, And MCP Policy

- The configured Codex-Hermes role-agent workflow is explicit authorization to use role subagents for non-trivial supervised tasks.
- Use role subagents according to their configured purpose: `atlas` for exploration, `prometheus` for planning/risk review, `hephaestus` for scoped implementation, `oracle` for verification/review, `librarian` for durable knowledge capture, and `scout` for narrow fast lookups.
- Keep role subagent calls scoped to the task and close them promptly after their result is collected.
- No silent fallback: configured models and reasoning efforts are part of the operating contract.
- If a configured model, reasoning effort, MCP server, skill, or custom agent is unavailable, surface the failure through doctor/checks instead of silently substituting another profile.
- Close subagents promptly after their result is collected so future role calls are not blocked by thread limits.

## Official Codex Harness Boundary

- Current production is App-primary mode: the official Codex App remains the primary UX/client.
- Codex-Hermes attaches through Supervisor MCP, AGENTS.md, Skills, custom agents, deterministic checks, managed file versioning, memory/wiki/search integration, and `harness_finish`.
- AppServer-supervised mode is a separate integration track and must not assume Desktop App internal proxying.
- AppServer-supervised mode must use stdio JSON-RPC by default.
- Controlled writes must stay behind Supervisor-mediated patch/write tools.

## Project Git Identity

- Project folders should have their own Git repository for Supervisor identity, Git guard, and finish gate behavior.
- If a project folder is not a Git repository, run the configured Supervisor
  Python before supervised work, normally:
  `<repo-root>\\codex-hermes-supervisor\\.venv\\Scripts\\python.exe -m codex_hermes_supervisor.cli project-git-ensure --repo <project-root>`.
- `py -m ...` is local convenience only when the package is intentionally
  installed into that Python.
- Existing Git repositories are used as-is.

## Persistence And Checkpoint Rules

- Keep moving toward the user's goal through implementation, verification, and handoff.
- Do not interrupt the user for routine progress that can be captured internally.
- Use `harness_checkpoint` for meaningful internal checkpoints: progress, decision, failed attempt, verification, risk, blocker, or note.
- If one approach fails, inspect evidence, record the failed attempt, try a safe alternate path, and continue.
- Stop and report only when no safe local action remains, required credentials or permissions are missing, user intent is risky to assume, or continuing could damage unrelated user work.
- When reporting a blocker, include what failed, evidence, alternatives tried, why remaining options require the user, and the exact next decision needed.
{_AGENTS_MD_END}
"""

_CUSTOM_AGENTS: dict[str, str] = {
    "atlas.toml": _AGENTS_HEADER
    + '''name = "atlas"
description = "Read-only codebase explorer. Use before planning or editing."
developer_instructions = """
You are atlas.
Explore the codebase and report concrete findings. Do not edit files. Focus on file locations, relevant symbols, and exact implementation details needed for planning.
"""
sandbox_mode = "read-only"
model = "gpt-5.5"
model_reasoning_effort = "low"
''',
    "scout.toml": _AGENTS_HEADER
    + '''name = "scout"
description = "Fast read-only scout. Use for narrow code lookups, log summaries, and small diff prechecks."
developer_instructions = """
You are scout.
Do fast, narrow investigation only. Use for locating files/symbols, summarizing short logs, or prechecking small diffs. Do not edit files. Do not make final plans, final reviews, or memory decisions.
"""
sandbox_mode = "read-only"
model = "gpt-5.3-codex-spark"
model_reasoning_effort = "high"
''',
    "prometheus.toml": _AGENTS_HEADER
    + '''name = "prometheus"
description = "Read-only planner and risk critic. Use after exploration and before implementation."
developer_instructions = """
You are prometheus.
Produce a precise execution plan and call out risks, invariants, and verification requirements. Do not edit files.
"""
sandbox_mode = "read-only"
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
''',
    "hephaestus.toml": _AGENTS_HEADER
    + '''name = "hephaestus"
description = "Implementation worker. Use only after Hermes recall and approved plan."
developer_instructions = """
You are hephaestus.
Implement the approved plan carefully. Keep changes scoped, preserve existing user work, and verify the result before handing back.
"""
sandbox_mode = "workspace-write"
model = "gpt-5.5"
model_reasoning_effort = "high"
''',
    "oracle.toml": _AGENTS_HEADER
    + '''name = "oracle"
description = "Read-only verifier and reviewer. Use after implementation to review diff, tests, regressions, and memory candidates."
developer_instructions = """
You are oracle.
Review the implementation for regressions, edge cases, and missing verification. Do not edit files. Report concrete findings with evidence.
"""
sandbox_mode = "read-only"
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
''',
    "librarian.toml": _AGENTS_HEADER
    + '''name = "librarian"
description = "Optional Obsidian/LLM Wiki maintainer. Use only when detailed knowledge capture is needed."
developer_instructions = """
You are librarian.
Capture durable knowledge in concise structured notes. Prefer decisions, bugs, workflows, and project memory over verbose narration.
"""
sandbox_mode = "read-only"
model = "gpt-5.5"
model_reasoning_effort = "low"
''',
}

_SKILLS: dict[str, str] = {
    "codex-hermes-harness": """---
name: codex-hermes-harness
description: Use only for non-trivial coding tasks that need supervisor state, scoped planning, implementation, verification, and handoff.
---

"""
    + _skills_marker("codex-hermes-harness")
    + """
# Codex-Hermes Harness

For non-trivial tasks:
1. Call `harness_begin`
2. Run `memory_lookup` for non-trivial or memory-dependent tasks
3. Explore with atlas
4. Plan with prometheus using the memory context
5. Implement with hephaestus
6. Verify with oracle
7. Call `harness_finish`

The configured harness role-agent workflow is explicit authorization from the
user to call these role subagents for non-trivial supervised tasks. Keep each
role call scoped to its purpose, do not substitute another model or role
silently, and close subagents promptly after their result is collected.

## Persistence Rules

- Continue through implementation, verification, and handoff unless blocked by a real external dependency or explicit user decision.
- Do not stop merely because one approach failed. Inspect logs, identify the root cause, try safe alternate approaches, and keep moving toward the task goal.
- Do not interrupt the user for routine progress updates. Record internal progress with `harness_checkpoint`.
- Use `harness_checkpoint` for progress, decisions, failed attempts, verification notes, risks, blockers, and other useful worklog entries.
- Close subagents promptly after their result is collected so future role calls are not blocked by thread limits.
- Stop and report only when no safe next action remains, required credentials or permissions are missing, user intent is risky to assume, or continuing could damage unrelated user work.
- Never mark work complete without verification. If verification cannot run, state exactly why and what remains unverified.

## Memory Rules

- Official LLM Wiki operating model is the standard: source intake, compilation,
  indexing, linking, query, writeback, and maintenance.
- The global `official-llm-wiki` Skill applies the standard to this stack.
- Obsidian CodexWiki is the current Markdown storage backend for LLM Wiki notes.
- Hermes is compact hot memory and pointer recall. `MEMORY.md` is for agent/project facts, conventions, tool quirks, reusable lessons, and pointers; `USER.md` is for user preferences, communication style, durable corrections, and things to avoid.
- Do not store raw logs, raw diffs, secrets, prompt-injection payloads, PDFs, screenshots, workbooks, raw private/customer data, or large code blocks in Hermes. Store compact sanitized lessons plus pointers to Obsidian CodexWiki notes.
- QMD and vector_local are retrieval selectors, not sources of truth. Read the referenced Markdown/source file before relying on a hit.
- For non-trivial work, consult Hermes recall and relevant Obsidian/QMD/vector memory before planning when available.
- Official LLM Wiki read-before-work is adaptive, not whole-vault reading on every task. Before planning, record one memory decision: `no_memory_needed`, `light_lookup`, `targeted_lookup`, or `deep_wiki_read`.
- `targeted_lookup` is required for previous-work/keyword/workstream references, continuing work, blockers or repeated failures, policy/design/config changes, multi-file or release risk, and explicit review/verification.
- `deep_wiki_read` is required for Official LLM Wiki / Hermes / Supervisor memory design, release handoff, cross-project import/promotion, architecture replacement, major refactors, and stale/superseded cleanup.
- Valid no-lookup skip reasons are `pure_chat`, `trivial_task`, `no_durable_outcome`, or `missing_project_memory_bootstrap` only when bootstrap is planned or checkpointed.
- If the user references previous work, a keyword, or a workstream name, call Supervisor `memory_lookup` before planning and use its context pack as memory evidence.
- `memory_lookup` is the implemented LLM Wiki lookup bridge: it searches Obsidian/QMD/vector artifacts, reads and hashes referenced Markdown sources, filters evidence to the current project or `scope: global`, and returns source paths plus rejected reference-only hits.
- Source ingest/compile expansion is governed by `docs/CODEX_HERMES_OFFICIAL_LLM_WIKI_SOURCE_INTAKE_AND_COMPILE_PLAN_V1.md`; it is CLI-first, dry-run-first, review-gated, and provenance-required until promoted to implemented baseline.
- Cross-project source or memory hits are reference-only until imported or promoted into the current project or reviewed as `scope: global`.
- `evidence_allowed` is computed at runtime by the evidence filter; do not store it as permanent note frontmatter.
- Hermes writeback during a task is for next-session recall, not same-session evidence. Current-task evidence must come from Supervisor state, Obsidian CodexWiki source reads, task records, and writeback queue.
- Future strict active-memory profile requires `memory_preflight` before `harness_plan`, `memory_evidence` in `harness_plan`, and writeback status in `harness_finish`. Until those tools exist, treat this as planned behavior and do not claim it is already enforced.
- Durable decisions, user corrections, bug root causes, project status changes, reusable workflows, source/provenance updates, architecture boundaries, and model/config/profile policy changes should be queued for Obsidian CodexWiki and/or compact Hermes writeback.

## Project-Scoped Memory Bootstrap

- Treat global AGENTS, Skills, MCP bindings, model policy, and role-agent rules as shared operating rules only.
- Treat project facts, decisions, bugs, workflows, source notes, task handoffs, and project lessons as project-scoped memory.
- Use `harness_begin` identity (`project_id`, `workspace_id`, `repo_root`) as the memory scope for non-trivial supervised work.
- Do not use cross-project Hermes recall or Obsidian notes as evidence unless the source is explicitly marked `scope: global`.
- If required project memory entrypoints are missing, create safe project-scoped bootstrap notes through Supervisor/Obsidian tools when available.
- If safe bootstrap creation is unavailable, record the missing files/notes with `harness_checkpoint` and report the exact bootstrap command or note path needed.
- `USER.md` is global user preference memory only. `MEMORY.md` is compact global agent memory and pointers; project facts belong in project-scoped Supervisor state and Obsidian CodexWiki notes.
- Project memory records should carry `scope`, `project_id`, `workspace_id`, `repo_root`, and `memory_kind` metadata whenever the storage format supports it.
""",
    "versioned-files": """---
name: versioned-files
description: Use only for managed/generated files that require immutable version snapshots and active mirrors.
---

"""
    + _skills_marker("versioned-files")
    + """
# Versioned Files

- Use `version_prepare` before editing managed files
- Edit only the pending version snapshot
- Use `version_sync` after editing
""",
    "official-llm-wiki": """---
name: official-llm-wiki
description: Apply the Official LLM Wiki operating model to the current Codex-Hermes and Obsidian CodexWiki backend.
---

"""
    + _skills_marker("official-llm-wiki")
    + """
# Official LLM Wiki

- Official LLM Wiki operating model is the standard: source intake, compilation into Markdown wiki pages, index/status/log maintenance, linking, query, writeback, and stale/superseded cleanup.
- This `official-llm-wiki` Skill applies that standard to the Codex-Hermes and Obsidian CodexWiki stack.
- Obsidian CodexWiki is the current Markdown storage backend for these notes.
- Read-before-work is adaptive, not whole-vault reading on every task. Before planning, choose and record `no_memory_needed`, `light_lookup`, `targeted_lookup`, or `deep_wiki_read`.
- Use at least `targeted_lookup` for previous-work/keyword/workstream references, continuing work, blockers, repeated failures, policy/design/config changes, multi-file risk, release/safety risk, or explicit review/verification.
- Use `deep_wiki_read` for Official LLM Wiki / Hermes / Supervisor memory design, release handoff, cross-project import or promotion, architecture replacement, major refactors, or stale/superseded cleanup.
- Use Supervisor `memory_lookup` when the user mentions a keyword, previous work, or workstream. It returns the project-scoped context pack and rejected reference-only hits.
- QMD/vector hits are selectors, not truth. Read and hash the referenced Markdown source before treating it as evidence.
- Source ingest and compile are governed by `docs/CODEX_HERMES_OFFICIAL_LLM_WIKI_SOURCE_INTAKE_AND_COMPILE_PLAN_V1.md`. Bulk ingest is not active until implemented and verified.
- Source ingest/compile must be CLI-first for bulk work, dry-run-first, review-gated for private/customer/secret/restricted material, and provenance-required.
- Cross-project source or memory hits are reference-only until imported or promoted into the current project or reviewed as `scope: global`.
- `evidence_allowed` is a runtime decision, not permanent note frontmatter.
- Hermes stores compact memory and pointers for next-session recall.
- Durable findings should be written back to Obsidian CodexWiki as project, workstream, task, decision, bug, workflow, source, status, or log notes.
- Never store raw logs, raw diffs, secrets, prompt-injection payloads, or large source dumps.
""",
}


def get_managed_agent_templates() -> dict[str, str]:
    """Return the current managed custom-agent templates."""

    return dict(_CUSTOM_AGENTS)


def _render_supervisor_config(config: SupervisorConfig, *, skill_root: Path) -> str:
    normalized_skill_root = normalize_windows_path(skill_root).replace("\\", "/")
    return (
        "config_schema_version: 1\n"
        "codex:\n"
        f"  skill_root: '{normalized_skill_root}'\n"
        "  skill_root_discovery:\n"
        "    - '%USERPROFILE%/.codex/skills'\n"
        "    - '%USERPROFILE%/.agents/skills'\n"
        "  create_skill_root_if_missing: true\n"
        "state:\n"
        "  root: '%USERPROFILE%/.codex-hermes/state'\n"
        f"  lock_timeout_seconds: {config.state.lock_timeout_seconds}\n"
        f"  heartbeat_seconds: {config.state.heartbeat_seconds}\n"
        "hermes:\n"
        f"  profile: '{config.hermes.profile}'\n"
        f"  mode: '{config.hermes.mode}'\n"
        f"  fail_open: {'true' if config.hermes.fail_open else 'false'}\n"
        "  outbox_dir: '%USERPROFILE%/.codex-hermes/hermes_outbox'\n"
        f"  read_builtin_files: {'true' if config.hermes.read_builtin_files else 'false'}\n"
        f"  cli_executable: '{config.hermes.cli_executable}'\n"
        "  cli_write_args:\n"
        + "".join(f"  - '{arg}'\n" for arg in config.hermes.cli_write_args)
        + "  cli_read_args:\n"
        + "".join(f"  - '{arg}'\n" for arg in config.hermes.cli_read_args)
        + f"  python_module: '{config.hermes.python_module}'\n"
        + f"  python_write_symbol: '{config.hermes.python_write_symbol}'\n"
        + f"  python_read_symbol: '{config.hermes.python_read_symbol}'\n"
        "obsidian:\n"
        f"  enabled: {'true' if config.obsidian.enabled else 'false'}\n"
        f"  vault_root: '{config.obsidian.vault_root}'\n"
        f"  wiki_root: '{config.obsidian.wiki_root}'\n"
        f"  link_style: '{config.obsidian.link_style}'\n"
        "search:\n"
        f"  backend: '{config.search.backend}'\n"
        "  vector_local:\n"
        f"    enabled: {'true' if config.search.vector_local.enabled else 'false'}\n"
        f"    dimensions: {config.search.vector_local.dimensions}\n"
        f"    auto_reindex: {'true' if config.search.vector_local.auto_reindex else 'false'}\n"
        "  qmd:\n"
        f"    enabled: {'true' if config.search.qmd.enabled else 'false'}\n"
        f"    executable: '{config.search.qmd.executable}'\n"
        "    command_prefix:\n"
        + "".join(f"    - '{arg}'\n" for arg in config.search.qmd.command_prefix)
        + f"    timeout_seconds: {config.search.qmd.timeout_seconds}\n"
        + "    collection_roots:\n"
        + "".join(f"    - '{normalize_windows_path(root)}'\n" for root in config.qmd_collection_roots)
    )


@dataclass
class InstallApplyResult:
    backup_dir: Path
    modified_files: list[Path]
    selected_skill_root: Path
    agent_root: Path


@dataclass
class _WriteResult:
    changed: bool
    action: str | None = None  # create | update | skip


def _backup_target(path: Path, backup_dir: Path, home: Path) -> Path:
    try:
        relative = path.resolve().relative_to(home.resolve())
    except ValueError:
        safe_name = normalize_windows_path(path).replace(":", "").replace("\\", "__")
        return backup_dir / "files" / safe_name
    return backup_dir / "files" / relative


def _backup_file(path: Path, backup_dir: Path, home: Path) -> None:
    if not path.exists():
        return
    target = _backup_target(path, backup_dir, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def _replace_or_append_block(text: str, begin: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(text):
        return pattern.sub(lambda _match: replacement, text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + replacement


def _render_mcp_section() -> str:
    supervisor_python = normalize_windows_path(Path(sys.executable)).replace("\\", "/")
    return _MCP_SECTION_BODY.replace("{{SUPERVISOR_PYTHON}}", supervisor_python)


def _upsert_mcp_section(text: str) -> str:
    mcp_section = _render_mcp_section()
    section_pattern = re.compile(
        r"(?ms)^\[mcp_servers\.codex_hermes_supervisor\]\n.*?(?=^\[[^\n]+\]|\Z)"
    )
    if section_pattern.search(text):
        return section_pattern.sub(mcp_section.rstrip() + "\n\n", text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + mcp_section


def _write_managed_file(path: Path, content: str, *, force: bool, backup_dir: Path, home: Path) -> _WriteResult:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing.startswith(_AGENTS_HEADER) or _is_managed_text(existing) or force:
            _backup_file(path, backup_dir, home)
            atomic_write_text(path, content)
            return _WriteResult(changed=True, action="update")
        _backup_file(path, backup_dir, home)
        return _WriteResult(changed=False, action="skip")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)
    return _WriteResult(changed=True, action="create")


def apply_install_global(
    config: SupervisorConfig,
    *,
    user_home: Path | None = None,
    force: bool = False,
) -> InstallApplyResult:
    """Apply managed user-scoped config, agents, and skills."""

    validate_generated_agent_templates(_CUSTOM_AGENTS)
    home = user_home or paths.user_home()
    backup_timestamp = now_local_iso().replace(":", "").replace("+", "_").replace("-", "")
    backup_dir = home / ".codex-hermes" / "backups" / backup_timestamp
    modified: list[Path] = []
    file_actions: list[dict[str, str]] = []

    codex_config = home / ".codex" / "config.toml"
    codex_config.parent.mkdir(parents=True, exist_ok=True)
    original_config = codex_config.read_text(encoding="utf-8") if codex_config.exists() else ""
    updated_config = _upsert_mcp_section(original_config)
    if updated_config != original_config:
        action = "update" if codex_config.exists() else "create"
        _backup_file(codex_config, backup_dir, home)
        atomic_write_text(codex_config, updated_config)
        modified.append(codex_config)
        file_actions.append({"path": normalize_windows_path(codex_config), "action": action})

    agents_md = home / ".codex" / "AGENTS.md"
    original_agents_md = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
    updated_agents_md = _replace_or_append_block(original_agents_md, _AGENTS_MD_BEGIN, _AGENTS_MD_END, _GLOBAL_AGENTS_BLOCK)
    if updated_agents_md != original_agents_md:
        action = "update" if agents_md.exists() else "create"
        _backup_file(agents_md, backup_dir, home)
        atomic_write_text(agents_md, updated_agents_md)
        modified.append(agents_md)
        file_actions.append({"path": normalize_windows_path(agents_md), "action": action})

    agent_root = home / ".codex" / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    for filename, content in _CUSTOM_AGENTS.items():
        path = agent_root / filename
        result = _write_managed_file(path, content, force=force, backup_dir=backup_dir, home=home)
        if result.changed:
            modified.append(path)
            file_actions.append({"path": normalize_windows_path(path), "action": result.action or "update"})

    skill_root, _ = config.discover_skill_root(user_home=home)
    skill_root.mkdir(parents=True, exist_ok=True)
    for skill_name, content in _SKILLS.items():
        skill_dir = skill_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        result = _write_managed_file(skill_file, content, force=force, backup_dir=backup_dir, home=home)
        if result.changed:
            modified.append(skill_file)
            file_actions.append({"path": normalize_windows_path(skill_file), "action": result.action or "update"})

    legacy_skill_file = skill_root / "llm-wiki-lite" / "SKILL.md"
    if legacy_skill_file.exists():
        legacy_text = legacy_skill_file.read_text(encoding="utf-8")
        if _is_managed_text(legacy_text):
            _backup_file(legacy_skill_file, backup_dir, home)
            legacy_skill_file.unlink()
            modified.append(legacy_skill_file)
            file_actions.append({"path": normalize_windows_path(legacy_skill_file), "action": "delete"})

    supervisor_root = home / ".codex-hermes"
    (supervisor_root / "logs").mkdir(parents=True, exist_ok=True)
    (supervisor_root / "cache").mkdir(parents=True, exist_ok=True)
    (supervisor_root / "state").mkdir(parents=True, exist_ok=True)
    (supervisor_root / "hermes_outbox").mkdir(parents=True, exist_ok=True)
    for template_path in ensure_project_templates(force=force):
        modified.append(template_path)
        file_actions.append({"path": normalize_windows_path(template_path), "action": "update"})

    supervisor_config_path = supervisor_root / "config.yaml"
    current_supervisor_config = supervisor_config_path.read_text(encoding="utf-8") if supervisor_config_path.exists() else ""
    rendered_supervisor_config = _render_supervisor_config(config, skill_root=skill_root)
    if current_supervisor_config != rendered_supervisor_config:
        action = "update" if supervisor_config_path.exists() else "create"
        _backup_file(supervisor_config_path, backup_dir, home)
        atomic_write_text(supervisor_config_path, rendered_supervisor_config)
        modified.append(supervisor_config_path)
        file_actions.append({"path": normalize_windows_path(supervisor_config_path), "action": action})

    manifest = {
        "timestamp": backup_timestamp,
        "backup_dir": normalize_windows_path(backup_dir),
        "modified_files": [normalize_windows_path(path) for path in modified],
        "file_actions": file_actions,
        "selected_skill_root": normalize_windows_path(skill_root),
        "agent_root": normalize_windows_path(agent_root),
    }
    atomic_write_text(backup_dir / "install_manifest.json", json.dumps(manifest, indent=2))
    return InstallApplyResult(
        backup_dir=backup_dir,
        modified_files=modified,
        selected_skill_root=skill_root,
        agent_root=agent_root,
    )


def rollback_install_global(timestamp: str, *, user_home: Path | None = None) -> list[Path]:
    """Restore backed up user-scoped files from a prior install run."""

    home = user_home or paths.user_home()
    backup_dir = home / ".codex-hermes" / "backups" / timestamp
    manifest_path = backup_dir / "install_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Backup manifest not found for {timestamp}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[Path] = []
    actions = manifest.get("file_actions")
    if not actions:
        actions = [{"path": path, "action": "update"} for path in manifest.get("modified_files", [])]
    for item in actions:
        original_path = Path(item["path"])
        backup_copy = _backup_target(original_path, backup_dir, home)
        action = item.get("action", "update")
        if action == "delete":
            if backup_copy.exists():
                original_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(original_path, backup_copy.read_text(encoding="utf-8"))
                restored.append(original_path)
            continue
        if action == "create":
            if original_path.exists():
                original_path.unlink()
                restored.append(original_path)
            continue
        if backup_copy.exists():
            original_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(original_path, backup_copy.read_text(encoding="utf-8"))
            restored.append(original_path)
    return restored
