from __future__ import annotations

from pathlib import Path

import pytest

from codex_hermes_supervisor.core.path_guard import PathPolicyError, normalize_repo_path


def test_reject_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(tmp_path, r"..\..\secret.env")


def test_reject_unc_path(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(tmp_path, r"\\server\share\file.txt")


def test_reject_extended_path(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(tmp_path, r"\\?\C:\Users\me\file.txt")


def test_reject_ads_path(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(tmp_path, r"file.txt:stream")


def test_reject_reserved_device_name(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(tmp_path, "CON.py")


def test_reject_trailing_dot(tmp_path: Path) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(tmp_path, "foo.")


def test_normalize_relative_inside_repo(tmp_path: Path) -> None:
    resolved = normalize_repo_path(tmp_path, r"src\main.py")
    assert resolved == (tmp_path / "src" / "main.py").resolve()
