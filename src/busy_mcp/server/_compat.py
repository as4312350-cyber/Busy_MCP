"""Differences between MCP Python SDK 1.x and 2.x, kept in one place.

SDK 2.x renamed ``FastMCP`` to ``MCPServer``, moved ``ToolError`` and switched
its pydantic models to snake_case fields with camelCase aliases. Fallbacks are
imported dynamically, so static type checkers only ever see the installed SDK
and the code stays valid for both major versions.
"""

from __future__ import annotations

import importlib
from typing import Any

from mcp.types import ToolAnnotations


def _first_available(*candidates: tuple[str, str]) -> Any:
    """Return the first ``module.attribute`` that can be imported."""
    errors = []
    for module_name, attribute in candidates:
        try:
            return getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{module_name}.{attribute}: {exc}")
    raise ImportError("No supported MCP Python SDK found (" + "; ".join(errors) + ")")


#: The MCP server class (``MCPServer`` in SDK 2.x, ``FastMCP`` in 1.x).
Server: Any = _first_available(("mcp.server.mcpserver", "MCPServer"), ("mcp.server.fastmcp", "FastMCP"))

#: Exception that SDKs report to the client as a clean tool error (``isError: true``).
ToolError: type[Exception] = _first_available(
    ("mcp.server.mcpserver.exceptions", "ToolError"), ("mcp.server.fastmcp.exceptions", "ToolError")
)


def read_only_annotations(title: str) -> ToolAnnotations:
    """Annotations marking a tool as read-only.

    Built from the protocol's wire names (camelCase), which SDK 1.x uses as
    field names and SDK 2.x accepts as aliases.
    """
    return ToolAnnotations.model_validate({
        "title": title,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    })


def create_server(name: str, instructions: str, version: str) -> Any:
    """Instantiate the SDK's server class (SDK 1.x has no ``version`` argument)."""
    try:
        return Server(name=name, instructions=instructions, version=version)
    except TypeError:
        return Server(name=name, instructions=instructions)
