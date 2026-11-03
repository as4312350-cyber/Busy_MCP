"""Command line: ``busy-mcp [serve|setup|doctor|verify]``.

``busy-mcp`` with no sub-command serves MCP over stdio, which is what AI
clients such as Claude Desktop launch.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from .. import __version__
from ..errors import BusyMCPError
from ..logging_setup import configure_logging


def _fy_year(text: str) -> int:
    """A financial year given by its start year: 2024, 2024-25 or 2024-2025 all mean 2024."""
    m = re.fullmatch(r"\s*(\d{4})(?:\s*-\s*(?:\d{2}|\d{4}))?\s*", text)
    if not m or not 1990 <= int(m.group(1)) <= 2100:
        raise argparse.ArgumentTypeError("use the year the financial year starts in, e.g. 2024 for 2024-25 "
                                         f"(got {text!r})")
    return int(m.group(1))


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

    verify = sub.add_parser(
        "verify", help="check busy-mcp's numbers on your own BUSY data and list five numbers to compare with BUSY",
        description="Checks busy-mcp's numbers on your own BUSY data (it only reads) and lists five numbers to "
                    "compare with BUSY's own screens. Exit code 1 means at least one check failed.")
    verify.add_argument("--company", help="company code, e.g. COMP0002 (default: the one busy-mcp normally uses)")
    verify.add_argument("--fy", type=_fy_year, metavar="YEAR",
                        help="financial year by its start year, e.g. 2024 for 2024-25 (default: the one busy-mcp "
                             "normally uses, else the latest)")
    verify.add_argument("--party", metavar="NAME", help="account to use for numbers 2 and 5 (default: the customer "
                                                        "with the largest balance / the most pending bills)")
    verify.add_argument("--item", metavar="NAME", help="item to use for number 4 (default: the most movement)")
    verify.add_argument("--json", action="store_true", help="print the report as JSON")
    verify.add_argument("--out", metavar="FILE", help="also save the report to FILE")
    verify.add_argument("--redact", action="store_true",
                        help='replace company, party and item names with "Party 1", "Item 1"... so the report '
                             "is safe to share")
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
        if command == "verify":
            from .verify import run_verify

            sys.exit(run_verify(args))
        from .serve import serve

        serve(http=args.http, host=args.host, port=args.port)
    except BusyMCPError as exc:
        sys.exit(f"Configuration error: {exc}\nRun 'busy-mcp doctor' for a full diagnosis.")
    except KeyboardInterrupt:
        sys.exit(130)
