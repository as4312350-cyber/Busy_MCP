"""The MCP SDK compatibility layer must work identically on SDK 1.x and 2.x."""

from __future__ import annotations

import pytest

from busy_mcp.server._compat import ToolError, _first_available, create_server, read_only_annotations


def test_annotations_mark_tools_read_only():
    dumped = read_only_annotations("Trial balance").model_dump(by_alias=True, exclude_none=True)
    assert dumped == {"title": "Trial balance", "readOnlyHint": True, "destructiveHint": False,
                      "idempotentHint": True, "openWorldHint": False}


def test_server_class_and_tool_error_resolve_for_installed_sdk():
    assert issubclass(ToolError, Exception)
    server = create_server("busy-accounting", "instructions", "0.0.0")
    assert callable(server.tool) and callable(server.prompt) and callable(server.resource)


def test_missing_sdk_is_reported_clearly():
    with pytest.raises(ImportError, match="No supported MCP Python SDK"):
        _first_available(("no_such_module_xyz", "Server"), ("json", "NoSuchAttribute"))
