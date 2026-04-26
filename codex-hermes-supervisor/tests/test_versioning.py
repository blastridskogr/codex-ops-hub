from __future__ import annotations

from pathlib import Path

from codex_hermes_supervisor.schemas.versioning import ManagedFileState
from codex_hermes_supervisor.services.versioning import prepare_version, sync_version, version_sort_key


def test_version_sort_key_handles_v010(tmp_path: Path) -> None:
    assert version_sort_key(tmp_path / "TEST_V0.9.py") < version_sort_key(tmp_path / "TEST_V0.10.py")


def test_prepare_version_imports_existing_active(tmp_path: Path) -> None:
    active = tmp_path / "tools" / "TEST.py"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("print('hello')\n", encoding="utf-8")
    data, state = prepare_version(active)
    assert Path(data.previous_version_path or "").name == "TEST_V0.1.py"
    assert Path(data.version_path).name == "TEST_V0.2.py"
    assert state.pending_version == data.version_path


def test_prepare_version_for_new_file_requires_explicit_allow(tmp_path: Path) -> None:
    active = tmp_path / "tools" / "TEST.py"
    data, state = prepare_version(active, allow_create=True)
    assert Path(data.version_path).name == "TEST_V0.1.py"
    assert state.pending_version == data.version_path


def test_sync_version_copies_bytes_exactly(tmp_path: Path) -> None:
    active = tmp_path / "tools" / "TEST.py"
    version = tmp_path / "tools" / "TEST_V0.1.py"
    version.parent.mkdir(parents=True, exist_ok=True)
    version.write_bytes(b"\x00\x01binary\r\n")
    state = ManagedFileState(active_path=str(active), versions=[str(version)], pending_version=str(version))
    data, updated = sync_version(active, version, existing_state=state)
    assert active.read_bytes() == version.read_bytes()
    assert data.active_hash == data.version_hash
    assert updated.pending_version is None
