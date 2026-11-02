"""Command line: ``busy-mcp [serve|setup|doctor]``.

``busy-mcp`` with no sub-command serves MCP over stdio, which is what AI
clients such as Claude Desktop launch.
"""

from __future__ import annotations

import argparse
import os
import sys

from .. import __version__
from ..errors import BusyMCPError
from ..logging_setup import configure_logging


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="busy-mcp", description="Read-only MCP server for BUSY Accounting Software")
    p.add_argument("--version", action="version", version=f"busy-mcp {__version__}")
    # legacy flags (1.0): `busy-mcp --check`, `busy-mcp --http`
    p.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--http", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--host", default=os.environ.get("BUSY_MCP_HOST", "127.0.0.1"), help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=int(os.environ.get("BUSY_MCP_PORT", "8765")), help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP server (default when no command is given)")
    serve.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    serve.add_argument("--host", default=os.environ.get("BUSY_MCP_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("BUSY_MCP_PORT", "8765")))

    setup = sub.add_parser("setup", help="guided setup: find BUSY data, test it, connect your AI app")
    setup.add_argument("--data-dir", help="BUSY data folder (Access edition)")
    setup.add_argument("--sql-server", help="SQL Server instance (SQL edition), e.g. localhost\\SQLEXPRESS")
    setup.add_argument("--sql-user", help="SQL login (omit for Windows authentication)")
    setup.add_argument("--company", help="default company code, e.g. COMP0001")
    setup.add_argument("--backend", choices=["access", "sqlserver", "sqlite"], help=argparse.SUPPRESS)
    setup.add_argument("--database", help=argparse.SUPPRESS)
    setup.add_argument("--claude-config", help="path of claude_desktop_config.json (auto-detected)")
    setup.add_argument("--no-claude", action="store_true", help="do not change Claude Desktop's configuration")
    setup.add_argument("--yes", "-y", action="store_true", help="accept defaults, no questions")

    sub.add_parser("doctor", help="check Python, drivers, BUSY data and configuration")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    configure_logging()
    command = args.command or ("doctor" if args.check else "serve")
    try:
        if command == "setup":
            from .wizard import run_setup

            sys.exit(run_setup(args))
        if command == "doctor":
            from .doctor import run_doctor

            sys.exit(run_doctor())
        from .serve import serve

        serve(http=args.http, host=args.host, port=args.port)
    except BusyMCPError as exc:
        sys.exit(f"Configuration error: {exc}\nRun 'busy-mcp doctor' for a full diagnosis.")
    except KeyboardInterrupt:
        sys.exit(130)
