"""Command-line entry point: ``busy-mcp``."""

from __future__ import annotations

import argparse
import os
import secrets
import sys

from .. import __version__
from ..db import fy_label
from ..errors import BusyMCPError
from ..logging_setup import configure_logging


def _check(state) -> int:  # type: ignore[no-untyped-def]
    from .. import reports

    try:
        targets = state.targets()
        print(f"Found {len(targets)} company/FY database(s):")
        for t in targets:
            print(f"  {t.key:<16} FY {fy_label(t.fy) or '?':<8} {t.describe()['location']}")
        db = state.db()
        ov = reports.company_overview(db)
        caps = reports.capabilities(db)
        tb = reports.trial_balance(db, None, None, ledger_level=False)
        print(f"Selected {db.target.key}  company={ov.get('company') or '-'}")
        print(f"  masters:  {ov['masters']}")
        print(f"  vouchers: {ov['vouchers']}")
        print(f"  cancelled columns: {caps['cancelled_voucher_columns'] or 'none found'}; "
              f"stock sign: {caps['item_inward_sign_rule']}")
        print(f"  trial balance difference: {tb['totals']['net_difference']}")
        print("OK")
        return 0
    except BusyMCPError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="busy-mcp", description="Read-only MCP server for BUSY Accounting")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("BUSY_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUSY_MCP_PORT", "8765")))
    parser.add_argument("--check", action="store_true", help="test the BUSY connection and exit")
    parser.add_argument("--version", action="version", version=f"busy-mcp {__version__}")
    args = parser.parse_args(argv)
    configure_logging()

    from .app import build_server

    try:
        mcp, state = build_server()
    except BusyMCPError as exc:
        sys.exit(f"Configuration error: {exc}")

    if args.check:
        sys.exit(_check(state))

    try:
        if not args.http:
            mcp.run("stdio")
            return
        _serve_http(mcp, args.host, args.port)
    finally:
        state.close()


def _serve_http(mcp, host: str, port: int) -> None:  # type: ignore[no-untyped-def]
    import uvicorn

    from .http import secure_app

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
