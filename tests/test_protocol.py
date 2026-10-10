"""MCP 2026-07-28 protocol and schema conformance tests."""

from __future__ import annotations

import pytest
from mcp import Client
from mcp_types import LATEST_PROTOCOL_VERSION

from elite3e_mcp.server import mcp


@pytest.mark.asyncio
async def test_server_discovers_protocol_revision_2026_07_28() -> None:
    assert LATEST_PROTOCOL_VERSION == "2026-07-28"

    async with Client(mcp, mode="auto") as client:
        assert client.protocol_version == "2026-07-28"
        tools = await client.list_tools()

    assert len(tools.tools) == 14


@pytest.mark.asyncio
async def test_every_tool_declares_descriptions_and_structured_output() -> None:
    tools = await mcp.list_tools()

    for tool in tools:
        assert tool.name == tool.name.lower()
        assert tool.description and "\n" not in tool.description.strip()
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        for parameter in tool.input_schema.get("properties", {}).values():
            assert parameter.get("description")
