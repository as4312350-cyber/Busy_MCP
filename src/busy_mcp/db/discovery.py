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


_SKIP_DIRS = frozenset({
    "windows", "$recycle.bin", "system volume information", "programdata", "appdata", "node_modules", ".git",
    "$windows.~bt", "recovery", "perflogs", "winsxs", "temp", "tmp",
})
_COMP_RE = re.compile(r"^COMP\d+$", re.IGNORECASE)


def scan_for_company_folders(roots: list[str], max_depth: int = 4, seconds: float = 25.0) -> list[str]:
    """Data folders (those holding COMPnnnn company folders) found under `roots`, searching `max_depth` levels
    down for at most `seconds`. Folders that can't be opened are skipped."""
    import time

    deadline = time.monotonic() + seconds
    found: list[str] = []
    queue: list[tuple[str, int]] = [(r, 0) for r in roots]
    while queue and time.monotonic() < deadline:
        folder, depth = queue.pop(0)
        try:
            with os.scandir(folder) as it:
                subdirs = [e for e in it if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        if any(_COMP_RE.match(e.name) for e in subdirs) and has_company_folders(folder):
            found.append(folder)
            continue  # the company folders are below this one; nothing else to look for in it
        if depth < max_depth:
            queue += [(e.path, depth + 1) for e in subdirs if e.name.lower() not in _SKIP_DIRS]
    return found


def open_companies(data_dir: str) -> list[str]:
    """Company folders (COMPnnnn) of `data_dir` that someone has open in BUSY right now. Microsoft's Access
    engine keeps a small lock file (.ldb or .laccdb) beside a database while it is open."""
    out: list[str] = []
    try:
        with os.scandir(data_dir) as it:
            comps = sorted(e.path for e in it if e.is_dir() and _COMP_RE.match(e.name))
    except OSError:
        return out
    for comp in comps:
        try:
            if any(n.lower().endswith((".ldb", ".laccdb")) for n in os.listdir(comp)):
                out.append(os.path.basename(comp))
        except OSError:
            continue
    return out


def data_dir_rank(path: str) -> tuple[int, float]:
    """Sort key, best first: a data folder with a company open in BUSY right now, then the one saved most recently."""
    newest = 0.0
    try:
        for comp in os.scandir(path):
            if comp.is_dir() and _COMP_RE.match(comp.name):
                for f in os.scandir(comp.path):
                    if f.name.lower().endswith(".bds"):
                        newest = max(newest, f.stat().st_mtime)
    except OSError:
        pass
    return (0 if open_companies(path) else 1, -newest)


def running_busy_folders() -> list[str]:
    """Folders around a BUSY program that is running now (Windows): where BUSY is installed usually shows where
    its data is. Empty elsewhere, or when BUSY isn't running."""
    if os.name != "nt":
        return []
    # One fixed, read-only command below; no user input reaches it.
    import subprocess  # nosec B404

    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'busy*' -and $_.ExecutablePath } "
          "| ForEach-Object { $_.ExecutablePath }")
    powershell = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")  # the full path: nothing in PATH or the working folder can stand in
    try:
        # Fixed arguments: it only lists the programs whose name starts with busy.
        out = subprocess.run(  # noqa: S603  # nosec B603
            [powershell, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    folders: list[str] = []
    for line in out.splitlines():
        exe = line.strip()
        if exe:
            here = os.path.dirname(exe)
            folders += [here, os.path.dirname(here)]
    return folders


def find_data_dirs(extra: list[str] | None = None) -> list[str]:
    """Best-effort search for BUSY data folders, best first: the one with a company open in BUSY right now, then
    the most recently saved. Looks next to a running BUSY, in the usual folders, and on every drive (mapped network
    drives included) a few levels down."""
    candidates: list[str] = list(extra or [])
    scan_roots: list[str] = []
    for here in running_busy_folders():
        candidates += [os.path.join(here, "DATA"), os.path.join(here, "Data"), here]
        scan_roots.append(here)
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
            scan_roots.append(root)
    seen: set[str] = set()
    found = []

    def add(c: str) -> None:
        key = os.path.normcase(os.path.abspath(c))
        if key not in seen and os.path.isdir(c) and has_company_folders(c):
            seen.add(key)
            found.append(c)

    for c in candidates:
        add(c)
    if not found:  # the quick checks found nothing: search the drives a few levels down
        for c in scan_for_company_folders(scan_roots):
            add(c)
    found.sort(key=data_dir_rank)
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
