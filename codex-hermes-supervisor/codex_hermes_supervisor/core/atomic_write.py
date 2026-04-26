"""Atomic write helpers."""

from __future__ import annotations

import os
from pathlib import Path


def _atomic_write(path: Path, write_mode: str, data: str | bytes, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, write_mode, encoding=encoding if "b" not in write_mode else None, newline="" if "b" not in write_mode else None) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    _atomic_write(path, "w", content, encoding=encoding)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    _atomic_write(path, "wb", content)
