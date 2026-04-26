"""Obsidian note writer and vault bootstrap helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from codex_hermes_supervisor.core.config import ObsidianConfig
from codex_hermes_supervisor.core.atomic_write import atomic_write_text
from codex_hermes_supervisor.core.locks import now_local_iso
from codex_hermes_supervisor.schemas.wiki import WikiNoteData, WikiNoteInput

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_TYPE_TO_DIR = {
    "task": "Tasks",
    "decision": "Decisions",
    "bug": "Bugs",
    "workflow": "Workflows",
    "project": "Projects",
    "source": "Sources",
}


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "note"


def _safe_stem(stem: str) -> str:
    stem = stem.strip(" .") or "note"
    if stem.lower() in _WINDOWS_RESERVED_NAMES:
        return f"note-{stem.lower()}"
    return stem


def _note_key(project_id: str, task_id: str, note_type: str, title: str) -> str:
    raw = f"{project_id}|{task_id}|{note_type}|{title.strip().lower()}"
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _max_stem_chars(directory: Path, *, ext: str, max_stem_chars: int, max_full_path_chars: int) -> int:
    directory_len = len(str(directory))
    budget = max_full_path_chars - directory_len - len("\\") - len(ext) - len(".tmp")
    return max(16, min(max_stem_chars, budget))


def _make_safe_wiki_path(
    directory: Path,
    *,
    title: str,
    note_key: str,
    max_stem_chars: int,
    max_full_path_chars: int,
    hash_suffix_chars: int,
) -> Path:
    ext = ".md"
    base_slug = _slug(title)
    hash_suffix = note_key.split(":", 1)[-1][:hash_suffix_chars]
    collision_index = 0
    while True:
        collision_suffix = "" if collision_index == 0 else f"-{collision_index + 1}"
        stem_budget = _max_stem_chars(
            directory,
            ext=ext,
            max_stem_chars=max_stem_chars,
            max_full_path_chars=max_full_path_chars,
        )
        reserved = len(hash_suffix) + len(collision_suffix) + 1
        base_budget = max(1, stem_budget - reserved)
        base = base_slug[:base_budget].rstrip("-") or "note"
        stem = _safe_stem(f"{base}-{hash_suffix}{collision_suffix}")
        path = directory / f"{stem}{ext}"
        tmp_path = directory / f"{stem}{ext}.tmp"
        if len(str(path)) <= max_full_path_chars and len(str(tmp_path)) <= max_full_path_chars:
            if not path.exists():
                return path
        collision_index += 1


def initialize_obsidian_vault(vault_root: Path, wiki_root: str) -> Path:
    """Create the minimum Obsidian wiki structure used by the harness."""

    wiki_dir = vault_root / wiki_root
    for dirname in ["Projects", "Tasks", "Decisions", "Bugs", "Workflows", "Sources", "_schema"]:
        (wiki_dir / dirname).mkdir(parents=True, exist_ok=True)

    index_path = wiki_dir / "_index.md"
    if not index_path.exists():
        atomic_write_text(
            index_path,
            "# CodexWiki\n\n"
            "- [[CodexWiki/Projects]]\n"
            "- [[CodexWiki/Tasks]]\n"
            "- [[CodexWiki/Decisions]]\n"
            "- [[CodexWiki/Bugs]]\n"
            "- [[CodexWiki/Workflows]]\n"
            "- [[CodexWiki/Sources]]\n",
        )

    schema_path = wiki_dir / "_schema" / "WIKI_SCHEMA.md"
    if not schema_path.exists():
        atomic_write_text(
            schema_path,
            "# WIKI_SCHEMA\n\n"
            "This vault stores Codex-Hermes Official LLM Wiki notes.\n\n"
            "- Frontmatter includes `wiki_schema_version`, `note_key`, `type`, `task_id`, `project_id`, `created`, and `updated`.\n"
            "- Notes are updated in place when the same `note_key` is written again.\n"
            "- Slug collisions for different `note_key` values receive `-2`, `-3`, and so on.\n",
        )

    return wiki_dir


def _existing_created_timestamp(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith('created: "') and line.endswith('"'):
            return line[len('created: "') : -1]
    return None


def write_wiki_note(
    vault_root: Path | None,
    wiki_root: str,
    project_id: str,
    note: WikiNoteInput,
    *,
    obsidian_config: ObsidianConfig | None = None,
) -> WikiNoteData:
    """Write or update an Obsidian note, or return disabled mode."""

    if vault_root is None:
        return WikiNoteData(created=False, disabled=True, message="Obsidian vault is not configured.")

    initialize_obsidian_vault(vault_root, wiki_root)
    directory_name = _TYPE_TO_DIR[note.type]
    directory = vault_root / wiki_root / directory_name
    directory.mkdir(parents=True, exist_ok=True)
    note_key = _note_key(project_id, note.task_id, note.type, note.title)
    defaults = obsidian_config or ObsidianConfig()

    existing_by_key: Path | None = None
    for candidate in directory.glob("*.md"):
        content = candidate.read_text(encoding="utf-8")
        if f'note_key: "{note_key}"' in content or f"note_key: '{note_key}'" in content:
            existing_by_key = candidate
            break

    path = existing_by_key or _make_safe_wiki_path(
        directory,
        title=note.title,
        note_key=note_key,
        max_stem_chars=defaults.max_filename_stem_chars,
        max_full_path_chars=defaults.max_full_path_chars,
        hash_suffix_chars=defaults.hash_suffix_chars,
    )

    created = _existing_created_timestamp(path) or now_local_iso()
    links_section = "\n".join(f"- {item}" for item in note.links) if note.links else "- none"
    body = (
        f"---\n"
        f'wiki_schema_version: 1\n'
        f'note_key: "{note_key}"\n'
        f'type: {note.type}\n'
        f'task_id: "{note.task_id}"\n'
        f'project_id: "{project_id}"\n'
        f'status: "reviewed"\n'
        f'confidence: "{note.confidence}"\n'
        f'created: "{created}"\n'
        f'updated: "{now_local_iso()}"\n'
        f"---\n\n"
        f"# {note.title}\n\n"
        f"## Summary\n\n{note.summary}\n\n"
        f"## Evidence\n\n" + ("\n".join(f"- {item}" for item in note.evidence) if note.evidence else "- none") + "\n\n"
        f"## Next Steps\n\n" + ("\n".join(f"- {item}" for item in note.next_steps) if note.next_steps else "- none") + "\n\n"
        f"## Links\n\n{links_section}\n"
    )
    try:
        atomic_write_text(path, body)
    except OSError as exc:
        return WikiNoteData(
            created=False,
            path=str(path),
            disabled=False,
            message=f"Wiki note creation failed: {exc}",
            warning_code="WIKI_NOTE_WRITE_FAILED",
        )
    return WikiNoteData(created=True, path=str(path), wikilink=f"[[{wiki_root}/{path.parent.name}/{path.stem}]]")
