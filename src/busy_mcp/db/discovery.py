"""Locating BUSY companies and financial-year databases."""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..errors import BusyConnectionError
from .drivers import pyodbc_module, sqlserver_conn_str

_FY_FILE_RE = re.compile(r"^db1(\d{4})\.bds$", re.IGNORECASE)
_COMP_DIR_RE = re.compile(r"^comp\d+$", re.IGNORECASE)
_SQL_DB_RE = re.compile(r"^busy(comp\d+)_db1(\d{4})$", re.IGNORECASE)


def fy_label(fy: int | None) -> str | None:
    return None if fy is None else f"{fy}-{str(fy + 1)[-2:]}"


def fy_bounds(fy: int | None) -> tuple[_dt.date, _dt.date] | None:
    return None if fy is None else (_dt.date(fy, 4, 1), _dt.date(fy + 1, 3, 31))


@dataclass(frozen=True)
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
            "location": self.location if self.backend != "odbc" else "custom ODBC connection",
        }


def resolve_backend(settings: Settings) -> str:
    if settings.backend != "auto":
        return settings.backend
    if settings.odbc_connection_string:
        return "odbc"
    if settings.sql_server:
        return "sqlserver"
    if settings.database and settings.database.lower().endswith((".sqlite", ".sqlite3")):
        return "sqlite"
    return "access"


def discover_targets(settings: Settings) -> list[Target]:
    backend = resolve_backend(settings)
    if backend == "odbc":
        return [Target("odbc", settings.company, settings.fy, "odbc", settings.odbc_connection_string)]
    if backend == "sqlite":
        if not settings.database:
            raise BusyConnectionError("BUSY_DATABASE must point to the .sqlite file for the sqlite backend")
        return [Target("sqlite", settings.company or "COMP0001", settings.fy, settings.database)]
    if backend == "sqlserver":
        if not settings.sql_server:
            raise BusyConnectionError("BUSY_SQL_SERVER must be set for the sqlserver backend")
        return _discover_sqlserver(settings)
    return _discover_access(settings)


def _discover_access(settings: Settings) -> list[Target]:
    if settings.database:
        path = settings.database
        m = _FY_FILE_RE.match(os.path.basename(path))
        comp = os.path.basename(os.path.dirname(path)).upper() or None
        return [Target("access", comp, int(m.group(1)) if m else settings.fy, path)]
    root = settings.data_dir
    if not os.path.isdir(root):
        raise BusyConnectionError(
            f"BUSY data folder not found: {root}. Set BUSY_DATA_DIR "
            "(BUSY > Company > 'Set Data Directory' shows the path)."
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
    pyodbc = pyodbc_module()
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
        raise BusyConnectionError(
            "No BUSY company databases were found. Check BUSY_DATA_DIR / BUSY_SQL_SERVER."
        )
    pool = targets
    if company:
        pool = [t for t in targets if (t.company or "").upper() == company.upper()]
        if not pool:
            raise BusyConnectionError(
                f"Company '{company}' not found. Available: {sorted({t.company or '?' for t in targets})}"
            )
    if fy:
        hit = [t for t in pool if t.fy == fy]
        if not hit:
            raise BusyConnectionError(
                f"FY {fy} not found for {pool[0].company}. Available: {sorted({t.fy or 0 for t in pool})}"
            )
        return hit[0]
    return max(pool, key=lambda t: t.fy or 0)
