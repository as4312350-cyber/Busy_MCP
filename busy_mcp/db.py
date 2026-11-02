"""Database access layer: discovers BUSY companies / financial years and opens
read-only connections to them.

Backends
  access    : BUSY standard edition, one MS-Access (.bds) file per company+FY
  sqlserver : BUSY SQL edition, one SQL Server database per company+FY
  odbc      : any full ODBC connection string you supply (BUSY_ODBC_CONNECTION_STRING)
  sqlite    : test/demo fixture only
"""

from __future__ import annotations

import datetime as _dt
import decimal
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import Settings

_FY_FILE_RE = re.compile(r"^db1(\d{4})\.bds$", re.IGNORECASE)
_COMP_DIR_RE = re.compile(r"^comp\d+$", re.IGNORECASE)
_SQL_DB_RE = re.compile(r"^busy(comp\d+)_db1(\d{4})$", re.IGNORECASE)


class BusyConnectionError(RuntimeError):
    pass


def _pyodbc():
    try:
        import pyodbc  # noqa: WPS433 (lazy: not needed for sqlite tests)
    except ImportError as exc:  # pragma: no cover - environment specific
        raise BusyConnectionError(
            "pyodbc is not installed. Run: pip install pyodbc"
        ) from exc
    return pyodbc


def fy_label(fy: int | None) -> str | None:
    if fy is None:
        return None
    return f"{fy}-{str(fy + 1)[-2:]}"


def fy_bounds(fy: int | None) -> tuple[_dt.date, _dt.date] | None:
    if fy is None:
        return None
    return _dt.date(fy, 4, 1), _dt.date(fy + 1, 3, 31)


@dataclass
class Target:
    """One BUSY company + financial year = one physical database."""

    backend: str  # access | sqlserver | odbc | sqlite
    company: str | None
    fy: int | None
    location: str  # .bds path, SQL database name, or sqlite path
    conn_str: str | None = None

    @property
    def key(self) -> str:
        if self.company and self.fy:
            return f"{self.company.upper()}/{self.fy}"
        return self.location

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "backend": self.backend,
            "company_code": self.company,
            "financial_year": fy_label(self.fy),
            "fy_start_year": self.fy,
            "location": self.location,
        }


# ---------------------------------------------------------------------------
# Connection strings
# ---------------------------------------------------------------------------


def _brace(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def _pick_sql_driver(settings: Settings) -> str:
    if settings.sql_driver:
        return settings.sql_driver
    drivers = _pyodbc().drivers()
    numbered = []
    for d in drivers:
        m = re.match(r"ODBC Driver (\d+) for SQL Server", d)
        if m:
            numbered.append((int(m.group(1)), d))
    if numbered:
        return max(numbered)[1]
    for fallback in ("SQL Server Native Client 11.0", "SQL Server"):
        if fallback in drivers:
            return fallback
    raise BusyConnectionError(
        "No SQL Server ODBC driver found. Install 'ODBC Driver 18 for SQL Server' from Microsoft."
    )


def _pick_access_driver(settings: Settings) -> str:
    drivers = _pyodbc().drivers()
    if settings.access_driver in drivers:
        return settings.access_driver
    for d in drivers:
        if "access driver" in d.lower():
            return d
    import struct

    bits = struct.calcsize("P") * 8
    raise BusyConnectionError(
        "No Microsoft Access ODBC driver visible to this "
        f"{bits}-bit Python. Install the 'Microsoft Access Database Engine 2016 "
        f"Redistributable' ({'x64' if bits == 64 else 'x86'}) or use a Python whose "
        "bitness matches the Access engine already installed with BUSY."
    )


def sqlserver_conn_str(settings: Settings, database: str) -> str:
    parts = [
        f"DRIVER={_brace(_pick_sql_driver(settings))}",
        f"SERVER={settings.sql_server}",
        f"DATABASE={_brace(database)}",
        "APP=busy-mcp",
        "ApplicationIntent=ReadOnly",
    ]
    if settings.sql_user:
        parts.append(f"UID={_brace(settings.sql_user)}")
        parts.append(f"PWD={_brace(settings.sql_password or '')}")
    else:
        parts.append("Trusted_Connection=yes")
    if settings.sql_trust_server_certificate:
        parts.append("TrustServerCertificate=yes")
    return ";".join(parts) + ";"


def access_conn_str(settings: Settings, path: str) -> str:
    parts = [
        f"DRIVER={_brace(_pick_access_driver(settings))}",
        f"DBQ={path}",
        "ReadOnly=1",
        "Exclusive=0",
    ]
    if settings.access_password:
        parts.append(f"PWD={_brace(settings.access_password)}")
    return ";".join(parts) + ";"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def resolve_backend(settings: Settings) -> str:
    if settings.backend != "auto":
        return settings.backend
    if settings.odbc_connection_string:
        return "odbc"
    if settings.sql_server:
        return "sqlserver"
    if settings.database and settings.database.lower().endswith((".sqlite", ".db", ".sqlite3")):
        return "sqlite"
    return "access"


def discover_targets(settings: Settings) -> list[Target]:
    backend = resolve_backend(settings)
    if backend == "odbc":
        return [Target("odbc", settings.company, settings.fy, "custom ODBC connection",
                       settings.odbc_connection_string)]
    if backend == "sqlite":
        if not settings.database:
            raise BusyConnectionError("BUSY_DATABASE must point to the .sqlite file for the sqlite backend")
        return [Target("sqlite", settings.company or "COMP0001", settings.fy, settings.database)]
    if backend == "sqlserver":
        return _discover_sqlserver(settings)
    if backend == "access":
        return _discover_access(settings)
    raise BusyConnectionError(f"Unknown BUSY_BACKEND '{backend}'")


def _discover_access(settings: Settings) -> list[Target]:
    if settings.database:
        path = settings.database
        m = _FY_FILE_RE.match(os.path.basename(path))
        comp = os.path.basename(os.path.dirname(path)) or None
        return [Target("access", comp, int(m.group(1)) if m else settings.fy, path)]
    root = settings.data_dir
    if not os.path.isdir(root):
        raise BusyConnectionError(
            f"BUSY data folder not found: {root}. Set BUSY_DATA_DIR (BUSY > Company > "
            "'Set Data Directory' shows the path)."
        )
    targets: list[Target] = []
    for comp in sorted(os.listdir(root)):
        cdir = os.path.join(root, comp)
        if not (os.path.isdir(cdir) and _COMP_DIR_RE.match(comp)):
            continue
        for fname in os.listdir(cdir):
            m = _FY_FILE_RE.match(fname)
            if m:
                targets.append(Target("access", comp.upper(), int(m.group(1)), os.path.join(cdir, fname)))
    targets.sort(key=lambda t: (t.company or "", -(t.fy or 0)))
    return targets


def _discover_sqlserver(settings: Settings) -> list[Target]:
    if settings.database:
        m = _SQL_DB_RE.match(settings.database)
        return [Target("sqlserver", m.group(1).upper() if m else settings.company,
                       int(m.group(2)) if m else settings.fy, settings.database)]
    pyodbc = _pyodbc()
    try:
        conn = pyodbc.connect(sqlserver_conn_str(settings, "master"), autocommit=True, timeout=15)
    except pyodbc.Error as exc:
        raise BusyConnectionError(f"Cannot connect to SQL Server '{settings.sql_server}': {exc}") from exc
    try:
        rows = conn.cursor().execute(
            "SELECT name FROM sys.databases WHERE state = 0 AND name LIKE 'Busy%'"
        ).fetchall()
    finally:
        conn.close()
    targets = []
    for (name,) in rows:
        m = _SQL_DB_RE.match(name)
        if m:
            targets.append(Target("sqlserver", m.group(1).upper(), int(m.group(2)), name))
    targets.sort(key=lambda t: (t.company or "", -(t.fy or 0)))
    return targets


def choose_target(targets: list[Target], company: str | None, fy: int | None) -> Target:
    if not targets:
        raise BusyConnectionError("No BUSY company databases were found. Check BUSY_DATA_DIR / BUSY_SQL_SERVER.")
    pool = targets
    if company:
        pool = [t for t in targets if (t.company or "").upper() == company.upper()]
        if not pool:
            raise BusyConnectionError(
                f"Company '{company}' not found. Available: {sorted({t.company for t in targets})}"
            )
    if fy:
        hit = [t for t in pool if t.fy == fy]
        if not hit:
            raise BusyConnectionError(
                f"FY {fy} not found for {pool[0].company}. Available: {sorted({t.fy for t in pool})}"
            )
        return hit[0]
    return max(pool, key=lambda t: t.fy or 0)


# ---------------------------------------------------------------------------
# Connection wrapper
# ---------------------------------------------------------------------------


def _clean(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, _dt.datetime):
        if value.time() == _dt.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, str):
        return value.strip()
    return value


@dataclass
class Master:
    code: int
    type: int
    name: str
    alias: str | None
    print_name: str | None
    parent: int


@dataclass
class BusyDB:
    target: Target
    settings: Settings
    _conn: Any = None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _masters: dict[int, Master] | None = None
    _masters_at: float = 0.0
    cache: dict[str, Any] = field(default_factory=dict)

    @property
    def dialect(self) -> str:
        if self.target.backend == "sqlite":
            return "sqlite"
        if self.target.backend == "access":
            return "access"
        if self.target.backend == "odbc" and "access" in (self.target.conn_str or "").lower():
            return "access"
        return "tsql"

    def top(self, n: int) -> str:
        return "" if self.dialect == "sqlite" else f"TOP {int(n)} "

    def limit(self, n: int) -> str:
        return f" LIMIT {int(n)}" if self.dialect == "sqlite" else ""

    def dparam(self, d: _dt.date) -> Any:
        """Date as a bind parameter in the form the driver expects."""
        if self.dialect == "sqlite":
            return d.isoformat()
        return _dt.datetime(d.year, d.month, d.day)

    # -- connection -----------------------------------------------------
    def _connect(self):
        t = self.target
        if t.backend == "sqlite":
            uri = f"file:{t.location}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            return conn
        pyodbc = _pyodbc()
        if t.backend == "access":
            if not os.path.exists(t.location):
                raise BusyConnectionError(f"BUSY data file not found: {t.location}")
            conn_str = access_conn_str(self.settings, t.location)
        elif t.backend == "sqlserver":
            conn_str = sqlserver_conn_str(self.settings, t.location)
        else:
            conn_str = t.conn_str or ""
        try:
            conn = pyodbc.connect(conn_str, autocommit=True, readonly=True, timeout=30)
        except pyodbc.Error as exc:
            hint = ""
            if t.backend == "access" and "password" in str(exc).lower():
                hint = " (wrong .bds password — set BUSY_ACCESS_PASSWORD)"
            raise BusyConnectionError(f"Cannot open {t.location}: {exc}{hint}") from exc
        try:
            conn.timeout = self.settings.query_timeout
        except Exception:  # noqa: BLE001 - not all drivers support it
            pass
        if self.dialect == "tsql" and self.settings.sql_read_uncommitted:
            conn.cursor().execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def query(self, sql: str, params: Iterable[Any] = (), max_rows: int | None = None) -> list[dict[str, Any]]:
        """Run a SELECT and return rows as dicts with lower-cased column keys."""
        with self._lock:
            for attempt in (1, 2):
                if self._conn is None:
                    self._conn = self._connect()
                try:
                    cur = self._conn.cursor()
                    cur.execute(sql, tuple(params))
                    cols = [d[0].lower() for d in cur.description or []]
                    raw = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
                    cur.close()
                    return [dict(zip(cols, (_clean(v) for v in row))) for row in raw]
                except Exception as exc:  # noqa: BLE001
                    if attempt == 1 and _is_disconnect(exc):
                        self.close()
                        continue
                    raise
        return []  # pragma: no cover

    def table_names(self) -> list[str]:
        with self._lock:
            if self.dialect == "sqlite":
                rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                return [r["name"] for r in rows]
            if self._conn is None:
                self._conn = self._connect()
            return sorted(r.table_name for r in self._conn.cursor().tables(tableType="TABLE"))

    def columns(self, table: str) -> list[dict[str, Any]]:
        with self._lock:
            if self.dialect == "sqlite":
                rows = self._sqlite_pragma(table)
                return [{"name": r[1], "type": r[2], "nullable": not r[3]} for r in rows]
            if self._conn is None:
                self._conn = self._connect()
            return [
                {"name": c.column_name, "type": c.type_name, "size": c.column_size,
                 "nullable": bool(c.nullable)}
                for c in self._conn.cursor().columns(table=table)
            ]

    def _sqlite_pragma(self, table: str):
        if self._conn is None:
            self._conn = self._connect()
        if not re.match(r"^[A-Za-z0-9_]+$", table):
            raise ValueError("Invalid table name")
        return self._conn.execute(f"PRAGMA table_info({table})").fetchall()

    # -- masters cache ---------------------------------------------------
    def masters(self, refresh: bool = False) -> dict[int, Master]:
        with self._lock:
            if refresh or self._masters is None or time.time() - self._masters_at > 300:
                rows = self.query("SELECT Code, MasterType, Name, Alias, PrintName, ParentGrp FROM Master1")
                self._masters = {
                    int(r["code"]): Master(
                        code=int(r["code"]),
                        type=int(r["mastertype"] or 0),
                        name=r["name"] or "",
                        alias=r.get("alias") or None,
                        print_name=r.get("printname") or None,
                        parent=int(r["parentgrp"] or 0),
                    )
                    for r in rows
                    if r.get("code") is not None
                }
                self._masters_at = time.time()
                self.cache.clear()
            return self._masters


def _is_disconnect(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("communication link", "connection is busy", "08s01", "08003",
                                   "connection was closed", "not connected"))
