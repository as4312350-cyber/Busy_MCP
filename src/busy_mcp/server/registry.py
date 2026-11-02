"""Tool registration helpers shared by every tool module.

Tool functions are plain synchronous functions. The registry runs each call in
a worker thread (ODBC is blocking), refreshes caches when BUSY data changed,
converts known errors into clean MCP tool errors, and marks every tool
read-only for clients.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio
from pydantic import Field

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]
from mcp.types import ToolAnnotations

from ..errors import BusyMCPError
from .state import State

log = logging.getLogger("busy_mcp.server")

# ---- shared parameter types (module level so the SDK can resolve them) ----
Company = Annotated[str | None, Field(description="Company code e.g. COMP0001 (default: selected company)")]
FY = Annotated[int | None, Field(ge=1990, le=2100,
                                 description="FY start year, e.g. 2024 = FY 2024-25 (default: selected year)")]
FromDate = Annotated[str | None, Field(description="Start date YYYY-MM-DD (default: FY start)")]
ToDate = Annotated[str | None, Field(description="End date YYYY-MM-DD (default: FY end)")]
AsOf = Annotated[str | None, Field(description="As-on date YYYY-MM-DD (default: FY end)")]
Offset = Annotated[int, Field(ge=0, description="Pagination offset (use next_offset from the previous page)")]
Limit = Annotated[int, Field(ge=1, le=1000, description="Page size")]
LedgerRef = Annotated[str, Field(min_length=1, max_length=200, description="Account name, alias or Code")]
ItemRef = Annotated[str, Field(min_length=1, max_length=200, description="Item name, alias or Code")]
OptionalText = Annotated[str | None, Field(max_length=200)]
MasterKind = Literal["group", "ledger", "item_group", "item"]
Nature = Literal["receivable", "payable"]
TableName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_]+$")]


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


class Registry:
    def __init__(self, mcp: Any, state: State):
        self.mcp = mcp
        self.state = state
        self.names: list[str] = []

    def tool(self, name: str, title: str, description: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter()
                try:
                    result = await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
                except BusyMCPError as exc:
                    log.info("tool %s rejected: %s", name, exc)
                    raise ToolError(str(exc)) from None
                except Exception as exc:
                    log.exception("tool %s failed", name)
                    raise ToolError(f"{type(exc).__name__}: {exc}") from None
                log.info("tool %s ok in %.0f ms", name, (time.perf_counter() - started) * 1000)
                return _json_safe(result)

            annotations = ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False)
            self.mcp.tool(name=name, description=description, annotations=annotations)(wrapper)
            self.names.append(name)
            return fn

        return deco
