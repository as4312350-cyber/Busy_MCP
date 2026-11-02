"""Locating BUSY companies and financial-year databases.

BUSY's data folder is often on another computer (``\\\\SERVER\\BusyWin\\DATA`` or a mapped drive). Folders are
read with ``os.scandir``: it reports which entries are folders without asking the server about each one again,
and a folder that can't be opened is reported with Windows' own reason.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..errors import BusyConnectionError
from .drivers import pyodbc_module, sqlserver_conn_str

log = logging.getLogger(__name__)

_FY_FILE_RE = re.compile(r"^db1(\d{4})\.bds$", re.IGNORECASE)
_COMP_DIR_RE = re.compile(r"^comp\d+$", re.IGNORECASE)
_SQL_DB_RE = re.compile(r"^busy(comp\d+)_db1(\d{4})$", re.IGNORECASE)
# \\server\share\... or //server/share/... (not the local device forms \\?\C:\ and \\.\), and \\?\UNC\server\share
_UNC_RE = re.compile(r"^[\\/]{2}(?![?.][\\/])[^\\/]+[\\/]+[^\\/]+")
_LONG_UNC_RE = re.compile(r"^[\\/]{2}[?.][\\/]UNC[\\/]+[^\\/]+[\\/]+[^\\/]+", re.IGNORECASE)
_DRIVE_REMOTE = 4  # GetDriveTypeW: a mapped network drive
_DATA_DIR_HINT = "Set BUSY_DATA_DIR (BUSY > Company > 'Set Data Directory' shows the path)."


def is_unc_path(path: str) -> bool:
    r"""True for a network path such as ``\\SERVER\BusyWin\DATA`` (also written ``//SERVER/...``)."""
    text = (path or "").strip().strip('"')
    return bool(_UNC_RE.match(text) or _LONG_UNC_RE.match(text))


def is_network_path(path: str) -> bool:
    """True if `path` is on another computer: a UNC path, or (Windows) a folder on a mapped network drive."""
    if is_unc_path(path):
        return True
    if os.name != "nt" or not path:
        return False
    try:
        import ctypes

        drive = os.path.splitdrive(os.path.abspath(path.strip().strip('"')))[0]
        if len(drive) != 2 or drive[1] != ":":
            return False
        return int(ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")) == _DRIVE_REMOTE  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return False


def _folder_error(path: str, exc: OSError) -> str:
    """Why a data folder can't be listed, in plain words (Windows' own reason included)."""
    reason = exc.strerror or str(exc)
    missing = isinstance(exc, (FileNotFoundError, NotADirectoryError))
    text = f"BUSY data folder {'not found' if missing else 'cannot be read'}: {path} ({reason})."
    if is_network_path(path):
        text += (" It is a network folder: the computer that shares it must be switched on, and the Windows user "
                 "that runs your AI app must be able to open this folder in File Explorer.")
        if not is_unc_path(path):
            text += (" Prefer its \\\\SERVER\\share\\... path to the drive letter: a mapped drive belongs to one "
                     "Windows sign-in and other programs may not see it.")
    return text


def _is_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _entries(path: str) -> list[os.DirEntry[str]]:
    with os.scandir(path) as it:
        return list(it)


def _fy_files(company_dir: str) -> list[tuple[str, int]]:
    """(file name, FY start year) of the financial-year databases in one COMPnnnn folder."""
    out = []
    for entry in _entries(company_dir):
        m = _FY_FILE_RE.match(entry.name)
        if m:
            out.append((entry.name, int(m.group(1))))
    return out


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
    if not root:
        raise BusyConnectionError(f"No BUSY data folder is configured. {_DATA_DIR_HINT}")
    try:
        companies = sorted((e.name, e.path) for e in _entries(root) if _COMP_DIR_RE.match(e.name) and _is_dir(e))
    except OSError as exc:
        raise BusyConnectionError(f"{_folder_error(root, exc)} {_DATA_DIR_HINT}") from exc
    targets: list[Target] = []
    for comp, cdir in companies:
        try:
            files = _fy_files(cdir)
        except OSError as exc:  # one unreadable company must not hide the others
            log.warning("skipping company folder %s: %s", cdir, exc)
            continue
        targets += [Target("access", comp.upper(), fy, os.path.join(cdir, fname)) for fname, fy in files]
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


def data_folder_problem(path: str) -> str | None:
    """None if `path` is a BUSY data folder (contains COMPnnnn\\db1YYYY.bds), otherwise what is wrong."""
    try:
        entries = _entries(path)
    except OSError as exc:
        return _folder_error(path, exc)
    for entry in entries:
        if _COMP_DIR_RE.match(entry.name) and _is_dir(entry):
            try:
                if _fy_files(entry.path):
                    return None
            except OSError:
                continue
    return f"'{path}' does not contain BUSY company folders (COMP0001\\db1YYYY.bds)."


def has_company_folders(path: str) -> bool:
    """True if `path` looks like a BUSY data folder (contains COMPnnnn\\db1YYYY.bds)."""
    return data_folder_problem(path) is None


def find_data_dirs(extra: list[str] | None = None) -> list[str]:
    """Best-effort search for BUSY data folders on this PC (Windows drives C: to Z:)."""
    candidates: list[str] = list(extra or [])
    if os.name == "nt":
        import string

        for letter in string.ascii_uppercase[2:]:
            root = f"{letter}:\\"
            if not os.path.exists(root):
                continue
            bases = [root, os.path.join(root, "Program Files (x86)"), os.path.join(root, "Program Files")]
            for base in bases:
                try:
                    entries = os.listdir(base)
                except OSError:
                    continue
                for entry in entries:
                    if "busy" in entry.lower():
                        for sub in ("DATA", "Data", "data", ""):
                            candidates.append(os.path.join(base, entry, sub))
    seen: set[str] = set()
    found = []
    for c in candidates:
        key = os.path.normcase(os.path.abspath(c))
        if key not in seen and os.path.isdir(c) and has_company_folders(c):
            seen.add(key)
            found.append(c)
    return found


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
