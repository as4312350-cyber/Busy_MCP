"""Read-only connection wrapper with a masters cache and runtime schema probing
(BUSY's column set varies between versions, so nothing optional is assumed)."""

from __future__ import annotations

import contextlib
import datetime as _dt
import decimal
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ..config import Settings
from ..errors import BusyConnectionError, ReportError
from .discovery import Target
from .drivers import access_conn_str, pyodbc_module, sqlserver_conn_str

log = logging.getLogger(__name__)
T = TypeVar("T")

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_KEYS = frozenset({"tables"})


def clean_value(value: Any) -> Any:
    """Convert driver values to JSON-friendly Python values."""
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


@dataclass(frozen=True)
class Master:
    code: int
    type: int
    name: str
    alias: str | None
    print_name: str | None
    parent: int
    inactive: bool = False


@dataclass
class BusyDB:
    target: Target
    settings: Settings
    _conn: Any = None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _masters: dict[int, Master] | None = None
    _masters_at: float = 0.0
    _cache: dict[str, Any] = field(default_factory=dict)
    _columns: dict[str, set[str]] = field(default_factory=dict)
    _fp: tuple[Any, ...] | None = None
    _checked_at: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ dialect
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
        """A date as a bind parameter in the form the driver expects."""
        if self.dialect == "sqlite":
            return d.isoformat()
        return _dt.datetime(d.year, d.month, d.day)

    # --------------------------------------------------------------- connection
    def _connect(self) -> Any:
        t = self.target
        if t.backend == "sqlite":
            if not os.path.exists(t.location):
                raise BusyConnectionError(f"Database file not found: {t.location}")
            return sqlite3.connect(f"file:{t.location}?mode=ro", uri=True, check_same_thread=False)
        pyodbc = pyodbc_module()
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
            text = str(exc)
            hint = ""
            if t.backend == "access" and "password" in text.lower():
                hint = " (wrong .bds password: set BUSY_ACCESS_PASSWORD)"
            elif "IM002" in text or "IM014" in text:
                hint = " (ODBC driver missing or 32/64-bit mismatch with this Python)"
            raise BusyConnectionError(f"Cannot open {t.key}: {text}{hint}") from exc
        try:
            conn.timeout = self.settings.query_timeout
        except Exception:  # noqa: BLE001 - Access driver does not support query timeouts
            log.debug("query timeout not supported by driver")
        if self.dialect == "tsql" and self.settings.sql_read_uncommitted:
            conn.cursor().execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        log.info("connected to %s (%s)", t.key, t.backend)
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    log.debug("error closing connection", exc_info=True)
                finally:
                    self._conn = None

    def __enter__(self) -> BusyDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _connection(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def query(self, sql: str, params: Iterable[Any] = (), max_rows: int | None = None) -> list[dict[str, Any]]:
        """Run a SELECT; rows are dicts keyed by lower-cased column name."""
        with self._lock:
            for attempt in (1, 2):
                cur = None
                try:
                    cur = self._connection().cursor()
                    cur.execute(sql, tuple(params))
                    cols = [d[0].lower() for d in cur.description or []]
                    raw = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
                    return [dict(zip(cols, (clean_value(v) for v in row), strict=False)) for row in raw]
                except BusyConnectionError:
                    raise
                except Exception as exc:
                    if attempt == 1 and _is_disconnect(exc):
                        log.warning("connection lost, reconnecting: %s", exc)
                        self.close()
                        continue
                    raise
                finally:
                    if cur is not None:
                        with contextlib.suppress(Exception):
                            cur.close()
        return []  # pragma: no cover

    # ------------------------------------------------------------ introspection
    def table_names(self) -> list[str]:
        with self._lock:
            if "tables" not in self._cache:
                if self.dialect == "sqlite":
                    rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                    self._cache["tables"] = [r["name"] for r in rows]
                else:
                    cur = self._connection().cursor()
                    self._cache["tables"] = sorted(r.table_name for r in cur.tables(tableType="TABLE"))
            return list(self._cache["tables"])

    def resolve_table(self, name: str) -> str:
        for t in self.table_names():
            if t.lower() == name.lower():
                return t
        raise ReportError(f"Unknown table '{name}'. Use list-tables.")

    def columns(self, table: str) -> list[dict[str, Any]]:
        table = self.resolve_table(table)
        with self._lock:
            if self.dialect == "sqlite":
                if not _SAFE_IDENT.match(table):
                    raise ReportError("Invalid table name")
                rows = self._connection().execute(f"PRAGMA table_info({table})").fetchall()
                return [{"name": r[1], "type": r[2], "nullable": not r[3]} for r in rows]
            cur = self._connection().cursor()
            return [
                {"name": c.column_name, "type": c.type_name, "size": c.column_size, "nullable": bool(c.nullable)}
                for c in cur.columns(table=table)
            ]

    def has_table(self, table: str) -> bool:
        return any(t.lower() == table.lower() for t in self.table_names())

    def column_set(self, table: str) -> set[str]:
        key = table.lower()
        with self._lock:
            if key not in self._columns:
                self._columns[key] = (
                    {c["name"].lower() for c in self.columns(table)} if self.has_table(table) else set()
                )
            return self._columns[key]

    def has_column(self, table: str, column: str) -> bool:
        return column.lower() in self.column_set(table)

    def existing_columns(self, table: str, wanted: Iterable[str]) -> list[str]:
        """The subset of `wanted` columns present in `table` (original spelling kept)."""
        have = self.column_set(table)
        return [c for c in wanted if c.lower() in have]

    def row_count(self, table: str) -> int:
        real = self.resolve_table(table)
        return int(self.query(f"SELECT COUNT(*) AS n FROM [{real}]")[0]["n"] or 0)

    # ------------------------------------------------------------- conventions
    def active_voucher_filter(self, alias: str = "t1") -> str:
        """SQL fragment (starting with ' AND') excluding cancelled vouchers, built
        only from columns that actually exist in this BUSY version."""
        if self.settings.include_cancelled:
            return ""
        parts = []
        for col in ("Cancelled", "VchCancelled"):
            if self.has_column("Tran1", col):
                parts.append(f" AND ({alias}.{col} = 0 OR {alias}.{col} IS NULL)")
        return "".join(parts)

    # ------------------------------------------------------- freshness & caching
    def _fingerprint(self) -> tuple[Any, ...]:
        """Cheap signature of the data: changes whenever a voucher or master is
        added, edited (where BUSY records modification times) or deleted."""
        parts: list[Any] = []
        for table, key in (("Tran1", "VchCode"), ("Master1", "Code")):
            extra = ", MAX(ModificationTime) AS mt" if self.has_column(table, "ModificationTime") else ""
            row = self.query(f"SELECT COUNT(*) AS n, MAX({key}) AS mx{extra} FROM {table}")[0]
            parts += [row.get("n"), row.get("mx"), row.get("mt")]
        if self.has_table("Folio1"):
            row = self.query("SELECT COUNT(*) AS n, SUM(D1) AS s FROM Folio1")[0]
            parts += [row.get("n"), round(float(row.get("s") or 0), 4)]
        return tuple(parts)

    def ensure_fresh(self, force: bool = False) -> bool:
        """Drop derived caches if BUSY data changed. Checks at most every
        ``freshness_seconds``. Returns True when caches were invalidated."""
        with self._lock:
            now = time.time()
            if not force and now - self._checked_at < self.settings.freshness_seconds:
                return False
            self._checked_at = now
            fp = self._fingerprint()
            too_old = bool(self.settings.cache_ttl) and now - self._masters_at > self.settings.cache_ttl
            if force or fp != self._fp or too_old:
                if self._fp is not None:
                    log.info("data changed in %s; refreshing caches", self.target.key)
                self._fp = fp
                self._masters = None
                self._cache = {k: v for k, v in self._cache.items() if k in _SCHEMA_KEYS}
                self.stats["data_changed_at"] = now
                return True
            return False

    def cached(self, key: str, fn: Callable[[], T]) -> T:
        """Memoise a derived result until the data changes."""
        with self._lock:
            if key not in self._cache:
                started = time.perf_counter()
                self._cache[key] = fn()
                self.stats.setdefault("build_ms", {})[key] = round((time.perf_counter() - started) * 1000)
            return self._cache[key]  # type: ignore[no-any-return]

    def is_cached(self, key: str) -> bool:
        return key in self._cache

    def masters(self, refresh: bool = False) -> dict[int, Master]:
        with self._lock:
            if refresh:
                self.ensure_fresh(force=True)
            if self._masters is None:
                started = time.perf_counter()
                inactive = ", DeactiveMaster" if self.has_column("Master1", "DeactiveMaster") else ""
                rows = self.query(f"SELECT Code, MasterType, Name, Alias, PrintName, ParentGrp{inactive} FROM Master1")
                self._masters = {
                    int(r["code"]): Master(
                        code=int(r["code"]),
                        type=int(r["mastertype"] or 0),
                        name=r["name"] or "",
                        alias=r.get("alias") or None,
                        print_name=r.get("printname") or None,
                        parent=int(r["parentgrp"] or 0),
                        inactive=bool(r.get("deactivemaster")),
                    )
                    for r in rows
                    if r.get("code") is not None
                }
                self._masters_at = time.time()
                self.stats["masters_ms"] = round((time.perf_counter() - started) * 1000)
                log.info("loaded %d masters from %s", len(self._masters), self.target.key)
            return self._masters

    def cache_info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "masters_loaded": self._masters is not None,
                "master_count": len(self._masters or {}),
                "cached_results": sorted(k for k in self._cache if k not in _SCHEMA_KEYS),
                "last_build_ms": dict(self.stats.get("build_ms", {})),
                "masters_load_ms": self.stats.get("masters_ms"),
                "seconds_since_data_change": (
                    round(time.time() - self.stats["data_changed_at"]) if "data_changed_at" in self.stats else None
                ),
                "freshness_check_every_s": self.settings.freshness_seconds,
            }


def _is_disconnect(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("communication link", "08s01", "08003", "connection was closed",
                                   "not connected", "connection is busy", "tcp provider"))
