"""Minimal Git probing for identity calculation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # MCP stdio servers must not let child git processes inherit the JSON-RPC
    # stdin stream or prompt for credentials.
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            env=_git_env(),
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    output = completed.stdout.strip()
    return output or None


def git_show_toplevel(cwd: Path) -> Path | None:
    output = _run_git(["rev-parse", "--show-toplevel"], cwd)
    return Path(output).resolve() if output else None


def git_remote_origin_url(cwd: Path) -> str | None:
    return _run_git(["config", "--get", "remote.origin.url"], cwd)


def git_common_dir(cwd: Path) -> Path | None:
    output = _run_git(["rev-parse", "--git-common-dir"], cwd)
    return (cwd / output).resolve() if output else None


def is_git_repo(cwd: Path) -> bool:
    return git_show_toplevel(cwd) is not None
