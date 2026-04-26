from __future__ import annotations

import os
import tempfile
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_stdio_mcp_protocol_initialize_and_list_tools() -> None:
    async def _main() -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            home.mkdir()

            server = StdioServerParameters(
                command="py",
                args=["-m", "codex_hermes_supervisor.mcp_server", "--profile", "coder"],
                env={**os.environ, "USERPROFILE": str(home), "HOME": str(home)},
                cwd=str(Path(__file__).resolve().parents[1]),
            )

            async with stdio_client(server) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    init = await session.initialize()
                    assert init.serverInfo.name == "codex-hermes-supervisor"

                    tools = await session.list_tools()
                    tool_names = {tool.name for tool in tools.tools}
                    assert "harness_apply_patch" in tool_names
                    assert "harness_begin" in tool_names
                    assert "harness_checkpoint" in tool_names
                    assert "harness_finish" in tool_names
                    assert "harness_write_version" in tool_names
                    assert "memory_lookup" in tool_names
                    assert len(tool_names) == 12

    anyio.run(_main)
