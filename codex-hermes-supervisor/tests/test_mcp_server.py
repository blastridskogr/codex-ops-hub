from __future__ import annotations

from codex_hermes_supervisor.mcp_server import build_server


def test_build_server_registers_all_tools() -> None:
    server = build_server("coder")
    tools = {tool.name for tool in server._tool_manager.list_tools()}
    assert tools == {
        "harness_apply_patch",
        "harness_begin",
        "harness_checkpoint",
        "harness_plan",
        "harness_check",
        "harness_finish",
        "harness_write_version",
        "lesson_capture",
        "memory_lookup",
        "version_prepare",
        "version_sync",
        "wiki_note",
    }


def test_build_server_does_not_write_stdout(capsys) -> None:
    build_server("coder")
    captured = capsys.readouterr()
    assert captured.out == ""
