"""``busy-mcp doctor``: explain in plain words what works and what to fix."""

from __future__ import annotations

import platform
import time
from typing import Any

from .. import __version__
from ..config import config_path, load_settings
from ..db import BusyDB, choose_target, discover_targets, find_data_dirs, resolve_backend
from ..db.drivers import python_bits
from ..errors import BusyMCPError


class Report:
    def __init__(self, out: Any = print) -> None:
        self.out = out
        self.failed = False

    def line(self, status: str, text: str) -> None:
        if status == "FAIL":
            self.failed = True
        self.out(f"[{status:^4}] {text}")


def run_doctor(out: Any = print) -> int:
    r = Report(out)
    out(f"busy-mcp doctor {__version__}")
    r.line("OK", f"Python {platform.python_version()} ({python_bits()}-bit) on "
                 f"{platform.system()} {platform.release()}")
    try:
        from importlib.metadata import version

        import mcp  # noqa: F401

        r.line("OK", f"MCP SDK {version('mcp')}")
    except Exception as exc:  # noqa: BLE001
        r.line("FAIL", f"MCP SDK not importable: {exc}")

    path = config_path()
    try:
        settings = load_settings()
    except BusyMCPError as exc:
        r.line("FAIL", f"Configuration: {exc}")
        return 1
    origin = ", ".join(f"{k} from {v}" for k, v in sorted(settings.origin.items())) or "defaults only"
    r.line("INFO", f"Config file: {path} ({'found' if path.exists() else 'not created yet'}); {origin}")
    backend = resolve_backend(settings)
    r.line("INFO", f"Edition/backend: {backend}")

    if backend != "sqlite":
        try:
            import pyodbc

            drivers = pyodbc.drivers()
            r.line("OK", f"pyodbc {pyodbc.version}; ODBC drivers: {', '.join(drivers) or 'none'}")
            if backend == "access" and not any("access driver" in d.lower() for d in drivers):
                r.line("FAIL", f"No Microsoft Access ODBC driver for this {python_bits()}-bit Python. Install the "
                               "'Microsoft Access Database Engine 2016 Redistributable' "
                               f"({'x64' if python_bits() == 64 else 'x86'}) or use Python of the other bitness "
                               "(BUSY usually installs the 32-bit engine).")
            if backend == "sqlserver" and not any("sql server" in d.lower() for d in drivers):
                r.line("FAIL", "No SQL Server ODBC driver. Install 'ODBC Driver 18 for SQL Server'.")
        except ImportError as exc:
            r.line("FAIL", f"pyodbc cannot load: {exc}")
            return 1

    try:
        targets = discover_targets(settings)
    except BusyMCPError as exc:
        r.line("FAIL", str(exc))
        if backend == "access":
            found = find_data_dirs()
            if found:
                r.line("INFO", f"BUSY data found at: {', '.join(found)} -> run 'busy-mcp setup'")
        return 1
    if not targets:
        r.line("FAIL", "No BUSY company databases found. Run 'busy-mcp setup'.")
        return 1
    shown = ", ".join(t.key for t in targets[:8]) + (" ..." if len(targets) > 8 else "")
    r.line("OK", f"Found {len(targets)} company/year database(s): {shown}")

    try:
        target = choose_target(targets, settings.company, settings.fy)
        db = BusyDB(target, settings)
        from .. import reports

        started = time.perf_counter()
        ov = reports.company_overview(db)
        name = (ov.get("company") or {}).get("name") or target.company
        r.line("OK", f"Opened {target.key} ({name}) in {(time.perf_counter() - started) * 1000:.0f} ms: "
                     f"{sum(ov['masters'].values())} masters, {ov['vouchers'].get('n')} vouchers")
        started = time.perf_counter()
        reports.ledger_index(db)
        reports.item_index(db)
        r.line("OK", f"Fast-read index built in {(time.perf_counter() - started) * 1000:.0f} ms")
        tb = reports.trial_balance(db, None, None, ledger_level=False)
        diff = tb["totals"]["net_difference"]
        if diff == "0.00":
            r.line("OK", "Trial balance tallies")
        else:
            r.line("WARN", f"Trial balance difference {diff}: {tb.get('warning', '')}")
        caps = reports.capabilities(db)
        present = [k for k, v in caps["features_present"].items() if v]
        r.line("INFO", f"BUSY features found: {', '.join(present) or 'core accounting only'}")
        r.line("INFO", f"Stock direction: {caps['item_inward_sign_rule']}; item lines: {caps['item_line_rectypes']}")
        db.close()
    except BusyMCPError as exc:
        r.line("FAIL", f"Cannot read {targets[0].key}: {exc}")
    except Exception as exc:  # noqa: BLE001 - doctor must report, not crash
        r.line("FAIL", f"Unexpected error: {type(exc).__name__}: {exc}")

    out("")
    out("Result: problems found - fix the [FAIL] lines above." if r.failed else
        "Result: ready. If your AI app is not connected yet, run 'busy-mcp setup'.")
    return 1 if r.failed else 0
