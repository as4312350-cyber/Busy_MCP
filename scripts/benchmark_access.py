r"""Measure busy-mcp on a large company stored like BUSY's Access edition, locally or in a network folder.

Many offices keep BUSY's data on one computer and open it from the others as \\SERVER\BusyWin\DATA. This
script builds a synthetic company in BUSY's layout (DATA\COMP0001\db12024.bds, an Access file protected with
BUSY's password) and times busy-mcp itself (BusyDB and the report functions) on it, once for every data folder
given, for example the local folder and the same folder through a network share. It also checks at that size
that the trial balance tallies, that the fast-read index equals direct SQL and that no voucher is lost.

    python scripts/benchmark_access.py                                  # 50,000 vouchers in a temporary folder
    python scripts/benchmark_access.py --root C:\BusyBench --build-only  # build C:\BusyBench\DATA, keep it
    python scripts/benchmark_access.py --data-dir C:\BusyBench\DATA --data-dir \\SERVER\BusyBench\DATA
    python scripts/benchmark_access.py --backend sqlite --vouchers 5000  # the same logic on SQLite, any OS

The company is the test company plus the synthetic rows of scripts/benchmark.py (populate: 2,000 parties,
1,000 items, N sales vouchers of 4 lines), plus one bill-by-bill reference per sale. The Access backend needs
Windows and an Access ODBC driver of this Python's bitness (32-bit Python always works: Windows ships the 32-bit
Jet driver). Never point --root at BUSY's own folder: the script refuses to build into a folder it didn't make.

Exit status: 0 = every check passed and every --assert-max-seconds limit was met; 1 = a check failed, a limit was
exceeded or a data folder could not be measured; 2 = bad command line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "tests")]

from benchmark import populate  # noqa: E402  (scripts/benchmark.py: the same synthetic vouchers)

from busy_mcp import __version__, reports  # noqa: E402
from busy_mcp.config import Settings, settings_from  # noqa: E402
from busy_mcp.db import BusyDB, choose_target, discover_targets, is_network_path  # noqa: E402
from busy_mcp.reports._common import LEDGER_INDEX, ledger_movements  # noqa: E402
from fixtures.busy_fixture import build  # noqa: E402

MANIFEST = "busy-mcp-benchmark.json"  # written next to COMP0001; tells a later run what was built
COMPANY, FY = "COMP0001", 2024
STEM = f"db1{FY}"
DEBTORS = 110  # "Sundry Debtors" in the test company; populate() puts its parties there
BATCH_ROWS = 2_000  # rows per Access transaction, well below Jet's default lock limit (MaxLocksPerFile 9,500)
DAY_BOOK = ("2024-06-01", "2024-06-30")
FIXTURE_ACCOUNTS = (202, 203, 204, 205, 206, 207, 208, 209, 210)  # bank, cash, parties, sales, purchase, ...
CHECK_PERIODS = ((None, dt.date(2025, 3, 31)), (dt.date(2024, 6, 1), dt.date(2024, 6, 30)),
                 (dt.date(2024, 6, 2), dt.date(2024, 6, 2)), (dt.date(2024, 10, 1), dt.date(2024, 12, 31)))

# Indexes on the columns busy-mcp's queries filter and join on. Real BUSY files come with their own set.
INDEXES = (  # name, table, columns, unique, what it serves
    ("ix_master1_code", "Master1", ("Code",), True, "master look-ups"),
    ("ix_folio1_master", "Folio1", ("MasterCode", "MasterType"), True, "opening balances"),
    ("ix_t1_vch", "Tran1", ("VchCode",), True, "joins from voucher lines, voucher look-ups"),
    ("ix_t1_date", "Tran1", ("Date",), False, "day book and dashboard periods"),
    ("ix_t1_type", "Tran1", ("VchType",), False, "dashboard sales and purchase totals"),
    ("ix_t2_vch", "Tran2", ("VchCode",), False, "lines of a voucher"),
    ("ix_t2_mc", "Tran2", ("MasterCode1", "RecType"), False, "one account's or item's entries"),
    ("ix_t3_mc", "Tran3", ("MasterCode1",), False, "bill-by-bill references of a party"),
    ("ix_t3_vch", "Tran3", ("VchCode",), False, "bill references of a voucher"),
)

PHASES = (  # key, what is timed
    ("connect", "find company, open file, list tables"),
    ("change_check", "change check before the first question"),
    ("masters", "load all masters"),
    ("overview", "company overview (setup and doctor)"),
    ("ledger_index", "account index: reads every voucher"),
    ("item_index", "item index: reads every item line"),
    ("trial_balance", "trial balance"),
    ("profit_and_loss", "profit & loss"),
    ("balance_sheet", "balance sheet"),
    ("stock_summary", "stock summary"),
    ("ledger_statement", "statement of the busiest party, full year"),
    ("bills_outstanding", "bill-wise receivables, all parties"),
    ("dashboard", "dashboard"),
    ("day_book", "day book, one month"),
    ("trial_balance_again", "trial balance again: from memory"),
    ("change_check_again", "change check before a later question"),
)
REPORT_PHASES = ("trial_balance", "profit_and_loss", "balance_sheet", "stock_summary", "ledger_statement",
                 "bills_outstanding", "dashboard", "day_book", "trial_balance_again")  # the "reports" limit
DERIVED = {"index": ("ledger_index", "item_index")}  # shown as a sum; "index" can carry a limit
LIMIT_NAMES = (*(k for k, _ in PHASES), *DERIVED, "reports")

_ACCESS_TYPES = {"INTEGER": "LONG", "REAL": "DOUBLE", "TEXT": "TEXT(255)"}
_DATE_COLUMN = re.compile(r"(Date|Time)$")  # dates are text in SQLite and Date/Time in Access, as in BUSY


def say(text: str = "") -> None:
    print(text, flush=True)


# ----------------------------------------------------------------------------------------------- build


def build_company(root: str, vouchers: int, backend: str) -> dict[str, Any]:
    """Build ``<root>\\DATA\\COMP0001\\db12024.bds`` (or ``.sqlite``) and its manifest; returns the manifest."""
    data = os.path.join(root, "DATA")
    company = os.path.join(data, COMPANY)
    _prepare(data)
    os.makedirs(company, exist_ok=True)
    with open(os.path.join(data, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump({"complete": False}, fh)  # marks the folder as ours, so a failed build can simply be rerun
    say(f"Building a synthetic BUSY company in {data}")
    started = time.perf_counter()
    work = tempfile.mkdtemp(prefix="busy-mcp-benchmark-src-")
    try:
        source = os.path.join(work, f"{STEM}.sqlite")
        build(source)
        populate(source, vouchers)
        _add_bill_references(source)
        facts = _facts(source, vouchers)
        generate_s = time.perf_counter() - started
        say(f"  generated with SQLite in {generate_s:.1f} s: {describe_company(facts)}")
        written = _write_access(source, company) if backend == "access" else _write_sqlite(source, company)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    path = os.path.join(company, written["file"])
    seconds = {"generate": round(generate_s, 1), **written.pop("seconds"),
               "total": round(time.perf_counter() - started, 1)}
    manifest = {
        "complete": True, "build_id": f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}", "busy_mcp": __version__,
        "backend": backend, "company": COMPANY, "fy": FY, **facts, **written,
        "file_mb": round(os.path.getsize(path) / 1_048_576, 1), "build_seconds": seconds,
    }
    with open(os.path.join(data, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    say(f"  built {path} ({manifest['file_mb']} MB) in {manifest['build_seconds']['total']:.1f} s")
    say("  indexes in the file (real BUSY files have their own; these are on the columns busy-mcp's queries use):")
    for ix in manifest["indexes"]:
        state = f"{ix['seconds']:6.1f} s" if ix["created"] else f"NOT CREATED: {ix['error']}"
        say(f"    {index_label(ix):<42} {state}  {ix['purpose']}")
    return manifest


def _prepare(data: str) -> None:
    """Build only into an empty folder or over an earlier benchmark company, never over real BUSY data."""
    if not os.path.isdir(data) or not os.listdir(data):
        return
    if not os.path.isfile(os.path.join(data, MANIFEST)):
        raise SystemExit(f"{data} already holds files and is not a benchmark company made by this script. "
                         "Choose an empty folder with --root (never BUSY's own data folder).")
    for name in (f"{STEM}.bds", f"{STEM}.mdb", f"{STEM}.ldb", f"{STEM}.sqlite"):
        path = os.path.join(data, COMPANY, name)
        if os.path.exists(path):
            os.remove(path)
    os.remove(os.path.join(data, MANIFEST))


def _add_bill_references(path: str) -> None:
    """One bill-by-bill reference per synthetic sale (Tran3, Method 1 = new bill, due in 30 days), as BUSY keeps
    for parties with bill-wise details, so that bills_outstanding has real work to do."""
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT INTO Tran3 (RefCode, RecType, Method, VchCode, VchType, MasterCode1, MasterCode2, [No], [Date], "
            "DueDate, Value1, Value3, NewRefAmount, Balance1, Balance2, Balance3, ItemSrNo) "
            "SELECT 100000 + VchCode, 1, 1, VchCode, VchType, MasterCode1, 0, VchNo, [Date], date([Date], '+30 days'), "
            "-VchAmtBaseCur, 0, 0, 0, 0, 0, 1 FROM Tran1 WHERE VchCode >= 1000")
        con.commit()
    finally:
        con.close()


def _facts(path: str, vouchers: int) -> dict[str, Any]:
    """What was built, and the answers a correct busy-mcp must give, from the SQLite source."""
    con = sqlite3.connect(path)
    try:
        busiest = con.execute(
            "SELECT t2.MasterCode1, COUNT(*) FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"INNER JOIN Master1 m ON m.Code = t2.MasterCode1 WHERE t2.RecType = 1 AND m.ParentGrp = {DEBTORS} "
            "AND t1.Cancelled = 0 AND t1.VchCancelled = 0 GROUP BY t2.MasterCode1 "
            "ORDER BY COUNT(*) DESC, t2.MasterCode1 LIMIT 1").fetchone()
        rows = {t: con.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                for t in ("Master1", "Tran1", "Tran2", "Tran3")}
        accounts = con.execute("SELECT COUNT(*) FROM Master1 WHERE MasterType = 2").fetchone()[0]
        items = con.execute("SELECT COUNT(*) FROM Master1 WHERE MasterType = 6").fetchone()[0]
        active = con.execute("SELECT COUNT(*) FROM Tran1 WHERE Cancelled = 0 AND VchCancelled = 0").fetchone()[0]
    finally:
        con.close()
    return {"vouchers": vouchers, "voucher_lines": 4 * vouchers, "bill_references": vouchers,
            "accounts": accounts, "items": items, "rows": rows, "expected_active_vouchers": active,
            "busiest_party": int(busiest[0]), "busiest_party_entries": int(busiest[1])}


def _access_columns(src: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    out = []
    for _, name, declared, *_ in src.execute(f"PRAGMA table_info([{table}])"):
        kind = (declared or "TEXT").upper()
        out.append((name, "DATETIME" if kind == "TEXT" and _DATE_COLUMN.search(name)
                    else _ACCESS_TYPES.get(kind, "TEXT(255)")))
    return out


def _write_access(source: str, company: str) -> dict[str, Any]:
    """Copy the SQLite company into a new password-protected Access file, fast: a transaction every BATCH_ROWS
    rows, and executemany over rows grouped by which columns are NULL (NULL columns are left out, because the
    Access driver can't describe parameters and may mistype a bound None)."""
    import pyodbc  # Windows only; pooling was switched off in main(), so close() really closes the file

    from fixtures.jet_fixture import PASSWORD, _create_empty, _set_password, access_driver

    driver = access_driver()
    mdb = os.path.join(company, f"{STEM}.mdb")
    started = time.perf_counter()
    _create_empty(mdb, driver, "mdb")
    password_set = _set_password(mdb, driver, False)
    say(f"  created an empty Access file with '{driver}'; BUSY's password {'set' if password_set else 'NOT set'}")
    conn = pyodbc.connect(f"DRIVER={{{driver}}};DBQ={mdb};" + (f"PWD={PASSWORD};" if password_set else ""),
                          autocommit=True)
    src = sqlite3.connect(source)
    try:
        cur = conn.cursor()
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        layout = {t: _access_columns(src, t) for t in tables}
        for table, columns in layout.items():
            cur.execute(f"CREATE TABLE [{table}] (" + ", ".join(f"[{n}] {k}" for n, k in columns) + ")")
        conn.autocommit = False
        copy_started = time.perf_counter()
        copied = {t: _copy_rows(src, conn, cur, t, layout[t]) for t in tables}
        copy_s = time.perf_counter() - copy_started
        conn.autocommit = True  # CREATE INDEX outside a transaction
        index_started = time.perf_counter()
        indexes = _create_indexes(cur, access=True)
        index_s = time.perf_counter() - index_started
        cur.close()
    finally:
        src.close()
        conn.close()
    os.replace(mdb, os.path.join(company, f"{STEM}.bds"))
    total_rows = sum(copied.values())
    rate = total_rows / max(copy_s, 1e-9)
    say(f"  copied {total_rows:,} rows into Access in {copy_s:.1f} s ({rate:,.0f} rows/s)")
    return {"file": f"{STEM}.bds", "password_set": password_set, "odbc_driver": driver, "indexes": indexes,
            "seconds": {"create": round(copy_started - started, 1), "copy": round(copy_s, 1),
                        "indexes": round(index_s, 1)}}


def _copy_rows(src: sqlite3.Connection, conn: Any, cur: Any, table: str, columns: list[tuple[str, str]]) -> int:
    names = [n for n, _ in columns]
    dates = [k == "DATETIME" for _, k in columns]
    reader = src.execute(f"SELECT * FROM [{table}]")
    started, total, report_at = time.perf_counter(), 0, 50_000
    while chunk := reader.fetchmany(BATCH_ROWS):
        groups: dict[tuple[bool, ...], list[list[Any]]] = {}
        for row in chunk:
            present = tuple(v is not None for v in row)
            groups.setdefault(present, []).append([
                dt.datetime.fromisoformat(v) if d and isinstance(v, str) else v
                for v, d, p in zip(row, dates, present, strict=True) if p])
        for present, rows in groups.items():
            cols = [f"[{n}]" for n, p in zip(names, present, strict=True) if p]
            if not cols:  # a row of NULLs only
                for _ in rows:
                    cur.execute(f"INSERT INTO [{table}] ([{names[0]}]) VALUES (NULL)")
                continue
            cur.executemany(f"INSERT INTO [{table}] ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
        conn.commit()
        total += len(chunk)
        if total >= report_at:
            say(f"    {table}: {total:,} rows, {time.perf_counter() - started:.0f} s")
            report_at += 50_000
    seconds = time.perf_counter() - started
    say(f"    {table:<18} {total:>9,} rows {seconds:7.1f} s")
    return total


def _write_sqlite(source: str, company: str) -> dict[str, Any]:
    started = time.perf_counter()
    shutil.copyfile(source, os.path.join(company, f"{STEM}.sqlite"))
    copy_s = time.perf_counter() - started
    con = sqlite3.connect(os.path.join(company, f"{STEM}.sqlite"))
    try:
        index_started = time.perf_counter()
        indexes = _create_indexes(con.cursor(), access=False)
        con.commit()
        index_s = time.perf_counter() - index_started
    finally:
        con.close()
    return {"file": f"{STEM}.sqlite", "password_set": False, "odbc_driver": None, "indexes": indexes,
            "seconds": {"copy": round(copy_s, 1), "indexes": round(index_s, 1)}}


def _create_indexes(cur: Any, access: bool) -> list[dict[str, Any]]:
    out = []
    for name, table, columns, unique, purpose in INDEXES:
        sql = (f"CREATE {'UNIQUE ' if unique else ''}INDEX {'' if access else 'IF NOT EXISTS '}[{name}] "
               f"ON [{table}] ({', '.join(f'[{c}]' for c in columns)})")
        entry: dict[str, Any] = {"name": name, "table": table, "columns": list(columns), "unique": unique,
                                 "purpose": purpose}
        started = time.perf_counter()
        try:
            cur.execute(sql)
            entry.update(created=True, seconds=round(time.perf_counter() - started, 2))
        except Exception as exc:  # noqa: BLE001 - reported in the output; the benchmark goes on without it
            entry.update(created=False, error=str(exc))
        out.append(entry)
    return out


def index_label(ix: dict[str, Any]) -> str:
    return f"{ix['table']} ({', '.join(ix['columns'])}){' unique' if ix['unique'] else ''}"


def describe_company(facts: dict[str, Any]) -> str:
    return (f"{facts['vouchers']:,} vouchers, {facts['voucher_lines']:,} voucher lines, "
            f"{facts['bill_references']:,} bill references, {facts['accounts']:,} accounts, {facts['items']:,} items")


# --------------------------------------------------------------------------------------------- measure


class Recorder:
    """Times one phase at a time and counts the SQL queries busy-mcp ran and the rows they returned."""

    def __init__(self, loc: dict[str, Any]) -> None:
        self.loc = loc
        self.queries = self.rows = 0
        self.current = ""

    def watch(self, db: BusyDB) -> None:
        original = db.query

        def query(sql: str, params: Any = (), max_rows: int | None = None) -> list[dict[str, Any]]:
            rows = original(sql, params, max_rows)
            self.queries += 1
            self.rows += len(rows)
            return rows

        db.query = query  # type: ignore[method-assign]

    def run(self, name: str, fn: Callable[[], Any]) -> Any:
        self.current = name
        queries, rows = self.queries, self.rows
        started = time.perf_counter()
        value = fn()
        seconds = time.perf_counter() - started
        phase = {"seconds": round(seconds, 4), "queries": self.queries - queries, "rows": self.rows - rows}
        self.loc["phases"][name] = phase
        say(f"  [{self.loc['n']}] {name:<20} {seconds:9.3f} s {phase['queries']:>4} SQL {phase['rows']:>9,} rows")
        return value


def read_manifest(data_dir: str) -> dict[str, Any]:
    try:
        with open(os.path.join(data_dir, MANIFEST), encoding="utf-8") as fh:
            manifest: dict[str, Any] = json.load(fh)
    except OSError as exc:
        raise RuntimeError(f"no benchmark company in {data_dir} ({exc.strerror or exc}); "
                           "build one first with --root") from exc
    if not manifest.get("complete"):
        raise RuntimeError(f"the benchmark company in {data_dir} was not finished; build it again with --root")
    return manifest


def measure_settings(data_dir: str, manifest: dict[str, Any]) -> Settings:
    # freshness_seconds=0: every ensure_fresh() really checks; cache_ttl=0: no time-based refresh mid-run.
    values: dict[str, Any] = {"company": manifest["company"], "fy": manifest["fy"], "freshness_seconds": 0,
                              "cache_ttl": 0}
    if manifest["backend"] == "access":
        from fixtures.jet_fixture import PASSWORD

        values.update(backend="access", data_dir=data_dir,
                      access_password=PASSWORD if manifest["password_set"] else "")
    else:
        values.update(backend="sqlite", database=os.path.join(data_dir, manifest["company"], manifest["file"]))
    return settings_from(values)


def check(loc: dict[str, Any], name: str, ok: bool, detail: str) -> None:
    loc["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    say(f"  [{loc['n']}] {'PASS' if ok else 'FAIL'}  {name}: {detail}")


def measure(n: int, data_dir: str) -> dict[str, Any]:
    """Every phase and check for one data folder; errors are recorded, not raised."""
    network = is_network_path(data_dir)
    loc: dict[str, Any] = {"n": n, "data_dir": data_dir, "network": network, "phases": {}, "checks": [],
                           "error": None}
    say(f"\n[{n}] {data_dir} ({'network folder' if network else 'local folder'})")
    rec = Recorder(loc)
    db: BusyDB | None = None
    try:
        manifest = loc["manifest"] = read_manifest(data_dir)
        settings = measure_settings(data_dir, manifest)
        if manifest["backend"] == "access":
            from busy_mcp.db.drivers import pick_access_driver

            loc["odbc_driver"] = pick_access_driver(settings)

        def connect() -> BusyDB:
            target = choose_target(discover_targets(settings), manifest["company"], manifest["fy"])
            opened = BusyDB(target, settings)
            rec.watch(opened)
            opened.table_names()
            return opened

        db = rec.run("connect", connect)
        rec.run("change_check", db.ensure_fresh)
        rec.run("masters", db.masters)
        overview = rec.run("overview", lambda: reports.company_overview(db))
        rec.run("ledger_index", lambda: reports.ledger_index(db))
        rec.run("item_index", lambda: reports.item_index(db))
        tb = rec.run("trial_balance", lambda: reports.trial_balance(db, None, None))
        rec.run("profit_and_loss", lambda: reports.profit_and_loss(db, None, None))
        rec.run("balance_sheet", lambda: reports.balance_sheet(db, None))
        rec.run("stock_summary", lambda: reports.stock_summary(db, None, None))
        statement = rec.run("ledger_statement",
                            lambda: reports.ledger_statement(db, str(manifest["busiest_party"]), None, None))
        rec.run("bills_outstanding", lambda: reports.bills_outstanding(db, "receivable"))
        rec.run("dashboard", lambda: reports.dashboard(db))
        rec.run("day_book", lambda: reports.day_book(db, *DAY_BOOK))
        tb_again = rec.run("trial_balance_again", lambda: reports.trial_balance(db, None, None))
        changed = rec.run("change_check_again", db.ensure_fresh)
        rec.current = "checks"

        totals = tb["totals"]
        check(loc, "trial balance tallies", totals["net_difference"] == "0.00",
              f"debit {totals['closing_dr']:,.2f} = credit {totals['closing_cr']:,.2f}, "
              f"difference {totals['net_difference']}")
        found = int(overview["vouchers"].get("n") or 0)
        check(loc, "every voucher counted", found == manifest["expected_active_vouchers"],
              f"{found:,} active vouchers read, {manifest['expected_active_vouchers']:,} built")
        _check_index_against_sql(loc, db, manifest)
        check(loc, "busiest party's statement complete", statement["total"] == manifest["busiest_party_entries"],
              f"{statement['total']:,} entries listed, {manifest['busiest_party_entries']:,} built "
              f"(party code {manifest['busiest_party']})")
        again = loc["phases"]["trial_balance_again"]
        check(loc, "repeat question answered from memory", again["queries"] == 0 and tb_again == tb,
              f"{again['queries']} SQL queries, same answer: {tb_again == tb}")
        check(loc, "change check keeps the index", changed is False and db.is_cached(LEDGER_INDEX),
              f"data unchanged, index kept: {changed is False and db.is_cached(LEDGER_INDEX)}")
    except Exception as exc:  # noqa: BLE001 - one unreachable folder must not hide the others' results
        loc["error"] = f"{rec.current or 'start'}: {type(exc).__name__}: {exc}"
        say(f"  [{n}] ERROR during {loc['error']}")
        traceback.print_exc()
    finally:
        if db is not None:
            db.close()
    return loc


def _check_index_against_sql(loc: dict[str, Any], db: BusyDB, manifest: dict[str, Any]) -> None:
    """Like tests/unit/test_index_and_freshness.py, at scale: 20 accounts x 4 periods, index vs direct SQL."""
    codes = list(dict.fromkeys([*FIXTURE_ACCOUNTS, manifest["busiest_party"], *range(10_007, 12_000, 199)]))[:20]
    started = time.perf_counter()
    wrong = []
    with BusyDB(db.target, db.settings) as direct:
        for code in codes:
            for start, end in CHECK_PERIODS:
                want = ledger_movements(direct, start, end, ledger=code).get(code, (0.0, 0.0))
                got = ledger_movements(db, start, end, ledger=code).get(code, (0.0, 0.0))
                if any(abs(a - b) > 0.01 for a, b in zip(want, got, strict=True)):
                    wrong.append(f"code {code} {start or 'start'}..{end}: SQL {want} vs index {got}")
        used_sql = not direct.is_cached(LEDGER_INDEX)
    seconds = time.perf_counter() - started
    loc["check_seconds"] = round(seconds, 2)
    check(loc, "index equals direct SQL", used_sql and not wrong,
          f"{len(codes)} accounts x {len(CHECK_PERIODS)} periods in {seconds:.1f} s"
          + ("" if used_sql else "; the direct side used the index") + "".join(f"; {w}" for w in wrong[:5]))


# ---------------------------------------------------------------------------------------------- report


def parse_limits(items: list[str] | None, parser: argparse.ArgumentParser) -> dict[str, float]:
    limits: dict[str, float] = {}
    for item in items or []:
        for part in item.split(","):
            name, sep, value = part.partition("=")
            name = name.strip()
            try:
                seconds = float(value)
            except ValueError:
                seconds = -1.0
            if not sep or name not in LIMIT_NAMES or seconds < 0:
                parser.error(f"--assert-max-seconds takes PHASE=SECONDS, PHASE one of {', '.join(LIMIT_NAMES)}; "
                             f"got {part!r}")
            limits[name] = seconds
    return limits


def phase_seconds(loc: dict[str, Any], name: str) -> float | None:
    if name in DERIVED:
        parts = [loc["phases"].get(p, {}).get("seconds") for p in DERIVED[name]]
        return None if None in parts else round(sum(p for p in parts if p is not None), 4)
    return loc["phases"].get(name, {}).get("seconds")


def violations(locations: list[dict[str, Any]], limits: dict[str, float]) -> list[str]:
    out = []
    for loc in locations:
        for name, limit in limits.items():
            names = [p for p in REPORT_PHASES if p not in limits] if name == "reports" else [name]
            for phase in names:
                seconds = phase_seconds(loc, phase)
                if seconds is not None and seconds > limit:
                    out.append(f"[{loc['n']}] {phase} took {seconds:.3f} s, limit {limit:g} s ({loc['data_dir']})")
    return out


def _cell(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds:.3f}"


def _first_phase(locations: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next((loc["phases"][key] for loc in locations if key in loc["phases"]), {})


def _setting_line(report: dict[str, Any]) -> str:
    c = report["company"]
    if not c:
        return "no company was measured"
    if c["backend"] == "access":
        storage = (f"Access file ({c['file_mb']} MB, BUSY's password {'set' if c['password_set'] else 'NOT set'}) "
                   f"read with '{report['machine'].get('odbc_driver') or c['odbc_driver']}'")
    else:
        storage = f"SQLite file ({c['file_mb']} MB)"
    m = report["machine"]
    return (f"{describe_company(c)}; {storage}; Python {m['python']} {m['bits']}-bit on {m['platform']}; "
            f"busy-mcp {report['busy_mcp']}")


def render_text(report: dict[str, Any]) -> str:
    locs = report["locations"]
    lines = ["", "=" * 100, f"busy-mcp on a large company: {_setting_line(report)}"]
    lines += [f"  [{loc['n']}] {loc['data_dir']} ({'network' if loc['network'] else 'local'} folder)" for loc in locs]
    head = "".join(f"{'[' + str(loc['n']) + ']':>11}" for loc in locs)
    lines += ["", f"{'phase (seconds)':<20} {'what':<42}{'SQL':>5}{'rows':>9}{head}"]
    for key, what in PHASES:
        first = _first_phase(locs, key)
        counts = f"{first['queries']:>5}{first['rows']:>9,}" if first else " " * 14
        cells = "".join(f"{_cell(phase_seconds(loc, key)):>11}" for loc in locs)
        lines.append(f"{key:<20} {what:<42}{counts}{cells}")
    for key, parts in DERIVED.items():
        cells = "".join(f"{_cell(phase_seconds(loc, key)):>11}" for loc in locs)
        lines.append(f"{key:<20} {' + '.join(parts):<42}{'':>14}{cells}")
    lines += ["", f"{'check':<63}{head}"]
    names = list(dict.fromkeys(c["name"] for loc in locs for c in loc["checks"]))
    for name in names:
        marks = "".join(f"{next(('pass' if c['ok'] else 'FAIL' for c in loc['checks'] if c['name'] == name), '-'):>11}"
                        for loc in locs)
        lines.append(f"{name:<63}{marks}")
    for loc in locs:
        if loc["error"]:
            lines.append(f"[{loc['n']}] ERROR {loc['error']}")
    lines += ["", *_result_lines(report)]
    return "\n".join(lines)


def _result_lines(report: dict[str, Any]) -> list[str]:
    limits = report["limits"]
    out = []
    if limits:
        out.append("Limits: " + ", ".join(f"{k} <= {v:g} s" + (" each" if k == "reports" else "")
                                          for k, v in limits.items()))
    out += [f"LIMIT EXCEEDED {v}" for v in report["violations"]]
    out.append("RESULT: " + ("OK - every check passed" + (" and every phase was within its limit" if limits else "")
                             if report["ok"] else "FAILED - see the FAIL, ERROR and LIMIT lines"))
    return out


def render_markdown(report: dict[str, Any]) -> str:
    locs = report["locations"]
    out = ["### busy-mcp: large company, local and network folder", "", _setting_line(report) + ".", "",
           "| # | Data folder | Kind |", "|---|---|---|"]
    out += [f"| {loc['n']} | `{loc['data_dir']}` | {'network (SMB)' if loc['network'] else 'local'} |" for loc in locs]
    nums = " | ".join(str(loc["n"]) for loc in locs)
    out += ["", f"| Phase | What | SQL queries | Rows read | {nums} |",
            "|---|---|---:|---:|" + "---:|" * len(locs)]
    for key, what in PHASES:
        first = _first_phase(locs, key)
        cells = " | ".join(_cell(phase_seconds(loc, key)) for loc in locs)
        out.append(f"| `{key}` | {what} | {first.get('queries', '')} | "
                   f"{format(first['rows'], ',') if first else ''} | {cells} |")
    for key, parts in DERIVED.items():
        cells = " | ".join(f"**{_cell(phase_seconds(loc, key))}**" for loc in locs)
        out.append(f"| **`{key}`** | {' + '.join(parts)} | | | {cells} |")
    out += ["", "Times in seconds.", "", f"| Check | {nums} |", "|---|" + "---|" * len(locs)]
    names = list(dict.fromkeys(c["name"] for loc in locs for c in loc["checks"]))
    for name in names:
        marks = " | ".join(next(("pass" if c["ok"] else "**FAIL**" for c in loc["checks"] if c["name"] == name), "-")
                           for loc in locs)
        out.append(f"| {name} | {marks} |")
    out += [f"\n**Error** in {loc['n']}: `{loc['error']}`" for loc in locs if loc["error"]]
    if report["company"]:
        out += ["", "Indexes in the benchmark file (real BUSY files have their own): "
                + ", ".join(index_label(ix) + ("" if ix["created"] else " (not created)")
                            for ix in report["company"]["indexes"]) + "."]
    out += [""] + [f"{line}  " for line in _result_lines(report)] + [""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Phases for --assert-max-seconds: " + ", ".join(LIMIT_NAMES)
                                       + " (reports = each report phase; index = ledger_index + item_index).")
    p.add_argument("--backend", choices=["access", "sqlite"], default="access",
                   help="what to build: an Access .bds file (Windows; default) or SQLite (any OS, to test the "
                        "logic). Measuring uses whatever was built")
    p.add_argument("--vouchers", type=int, default=50_000, help="sales vouchers to generate (default 50,000)")
    p.add_argument("--root", help="build the company in ROOT\\DATA (default: a temporary folder, deleted after)")
    p.add_argument("--build-only", action="store_true", help="build in --root and stop")
    p.add_argument("--data-dir", action="append", metavar="DIR",
                   help="data folder to measure, e.g. a \\\\SERVER\\share\\DATA path (repeatable). Without --root "
                        "nothing is built: DIR must hold a company built earlier by this script")
    p.add_argument("--json", default="busy-benchmark.json", metavar="PATH",
                   help="where to write the results (default busy-benchmark.json)")
    p.add_argument("--summary-md", metavar="PATH", help="append a Markdown table to PATH, e.g. $GITHUB_STEP_SUMMARY")
    p.add_argument("--assert-max-seconds", action="append", metavar="PHASE=SECONDS",
                   help="fail if PHASE takes longer (repeatable), e.g. index=180 reports=10")
    args = p.parse_args(argv)
    limits = parse_limits(args.assert_max_seconds, p)
    if args.vouchers < 0:
        p.error("--vouchers must be 0 or more")
    if args.build_only and (not args.root or args.data_dir):
        p.error("--build-only needs --root (a temporary folder would be deleted) and no --data-dir")
    if args.backend == "access" and (args.root or not args.data_dir) and os.name != "nt":
        p.error("building an Access file needs Windows with an Access ODBC driver; use --backend sqlite here")
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:  # a folder name the console code page can't show must not stop the run
        reconfigure(errors="backslashreplace")
    if os.name == "nt":
        try:
            import pyodbc

            pyodbc.pooling = False  # before the first ODBC call: a pooled connection keeps the file open
        except ImportError:
            pass

    build_root: str | None = args.root
    temp_root: str | None = None
    if not build_root and not args.data_dir:
        build_root = temp_root = tempfile.mkdtemp(prefix="busy-mcp-benchmark-")
    built = None
    try:
        data_dirs: list[str] = args.data_dir or []
        if build_root:
            build_root = os.path.abspath(build_root)
            built = build_company(build_root, args.vouchers, args.backend)
            if args.build_only:
                return 0
            data_dirs = data_dirs or [os.path.join(build_root, "DATA")]
        locations = [measure(i, d) for i, d in enumerate(data_dirs, 1)]
    finally:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)

    company = built or next((loc["manifest"] for loc in locations if loc.get("manifest")), None)
    for loc in locations:
        if company and loc.get("manifest") and loc["manifest"]["build_id"] != company["build_id"]:
            loc["error"] = loc["error"] or f"a different benchmark company ({loc['manifest']['build_id']})"
        loc.pop("manifest", None)
    report: dict[str, Any] = {
        "benchmark": "busy-mcp scripts/benchmark_access.py", "busy_mcp": __version__,
        "finished": dt.datetime.now().isoformat(timespec="seconds"),
        "machine": {"python": platform.python_version(), "bits": struct.calcsize("P") * 8,
                    "platform": platform.platform(),
                    "odbc_driver": next((loc["odbc_driver"] for loc in locations if loc.get("odbc_driver")), None)},
        "company": company, "limits": limits, "locations": locations,
    }
    report["violations"] = violations(locations, limits)
    report["ok"] = bool(locations) and not report["violations"] and all(
        not loc["error"] and loc["checks"] and all(c["ok"] for c in loc["checks"]) for loc in locations)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    if args.summary_md:
        with open(args.summary_md, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(report) + "\n")
    say(render_text(report))
    say(f"Results: {os.path.abspath(args.json)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
