# Release Checklist

Use this checklist before publishing a public Codex Ops Hub release.

## Source Pin

- Repository: `https://github.com/blastridskogr/codex-ops-hub.git`
- Release ref: `codex-ops-hub-v0.1.0`
- Supervisor package path: `codex-hermes-supervisor`

## Clean Clone Verification

Run from a fresh clone:

```powershell
git clone https://github.com/blastridskogr/codex-ops-hub.git C:\tmp\codex-ops-hub-clean
cd C:\tmp\codex-ops-hub-clean
git checkout codex-ops-hub-v0.1.0
cd .\codex-hermes-supervisor

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m compileall -q codex_hermes_supervisor
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m codex_hermes_supervisor.cli doctor --codex-compat
```

Expected:

- `compileall` exits 0.
- tests pass.
- doctor reports Codex compatibility status without crashing.

## Public Safety Scan

Check for stale or unsafe public wording:

```powershell
$docPaths = @(".\README.md", ".\THIRD_PARTY_NOTICES.md") +
  (Get-ChildItem .\docs\*.md | Where-Object { $_.Name -ne "RELEASE_CHECKLIST.md" }).FullName

Select-String -Path $docPaths -Pattern `
  (@(
    ("LLM Wiki Lite is the " + "standard"),
    ("LLM Wiki Lite " + "protocol"),
    ("memories " + "= true"),
    ("automatic destructive " + "rollback"),
    ("automatic " + "revert"),
    ("task-level " + "rollback"),
    "--repo\s+--dry-run",
    ("Projects/" + "/"),
    ("Tasks/" + "/")
  ) -join "|")
```

Expected: no hits.

Check that private local material is not tracked:

```powershell
git grep -n -I -E "C:\\Users\\[^\\]+|C:/Users/[^/]+|codex-telegram|ObsidianVault|audit\.log|tmp_app|node_modules|__pycache__|\.pytest_cache"
```

Expected: only public examples, `.gitignore` entries, or placeholder paths.

## Release Rules

- Do not publish private Obsidian vault content.
- Do not publish Hermes outbox records, task records, sessions, raw customer files, screenshots, workbooks, or archives.
- Do not claim bulk source ingest, heavy-source ingest, or compile apply is
  implemented until the CLI-first, dry-run-first, review-gated implementation is
  verified.
- Do not treat QMD/vector hits as evidence without source read, hash/fingerprint capture, and runtime project/scope filtering.
- Do not store `evidence_allowed` as permanent frontmatter; compute it at runtime.
