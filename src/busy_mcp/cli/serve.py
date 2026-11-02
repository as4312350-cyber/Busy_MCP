"""Running the MCP server over stdio or streamable HTTP."""

from __future__ import annotations

import os
import secrets
import sys
from typing import Any

from .. import __version__


def serve(http: bool = False, host: str = "127.0.0.1", port: int = 8765) -> None:
    from ..server import build_server

    mcp, state = build_server()
    if state.settings.prefetch:
        state.prefetch()
    try:
        if http:
            _serve_http(mcp, host, port)
        else:
            mcp.run("stdio")
    finally:
        state.close()


def _serve_http(mcp: Any, host: str, port: int) -> None:
    import uvicorn

    from ..server.http import secure_app

    token = os.environ.get("BUSY_MCP_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        print(f"BUSY_MCP_TOKEN not set; generated one for this run:\n  {token}", file=sys.stderr)
    elif len(token) < 16:
        sys.exit("BUSY_MCP_TOKEN must be at least 16 characters.")
    origins = [o.strip() for o in os.environ.get("BUSY_MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    app = secure_app(mcp.streamable_http_app(), token, origins)
    print(f"busy-mcp {__version__} listening on http://{host}:{port}/mcp (Bearer token required)", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="warning", server_header=False)
