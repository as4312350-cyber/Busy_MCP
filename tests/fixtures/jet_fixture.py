"""The test company as a real Microsoft Access database, laid out like BUSY's (``COMP0001\\db12024.bds``).

Windows only. The Python must have the same bitness as an installed Access ODBC driver; Windows itself
ships the 32-bit Jet driver, so 32-bit Python always works. Like BUSY's own files, the copy is protected
with BUSY's default password, so the tests open it exactly the way busy-mcp opens a user's data.

Two choices, made with pytest options (see ``tests/conftest.py``):

* the driver that builds the file, which busy-mcp then also reads it with: Windows' built-in Jet driver,
  ``Microsoft Access Driver (*.mdb)``, or the Access Database Engine's (ACE), ``Microsoft Access Driver
  (*.mdb, *.accdb)``, which most BUSY integrations use. ``auto`` takes ACE when it is installed.
* the file format: ``.mdb`` (Jet 4), which both drivers read, or ``.accdb`` (Access 2007 and later), which
  only ACE reads.

The header of the new file is checked and printed, so a test log shows which format was really tested.

Used by ``pytest --backend access`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import re
import sqlite3
import struct
import time
from typing import Any

PASSWORD = "ILoveMyINDIA"  # BUSY's publicly known default .bds password
DRIVER_CHOICES = ("auto", "jet", "ace")
FORMAT_CHOICES = ("mdb", "accdb")
_DATE_COLUMN = re.compile(r"(Date|Time)$")  # BUSY keeps these as Access Date/Time; the SQLite copy as text
_TYPES = {"INTEGER": "LONG", "REAL": "DOUBLE", "TEXT": "TEXT(255)"}
# Every Access file starts with 00 01 00 00 and a 15-byte engine name; byte 0x14 is the version.
_MAGIC = b"\x00\x01\x00\x00"
_ENGINE_NAMES = {"mdb": b"Standard Jet DB", "accdb": b"Standard ACE DB"}
_VERSIONS = {0: "Jet 3", 1: "Jet 4", 2: "ACE 12", 3: "ACE 14", 5: "ACE 16", 6: "ACE 17"}
# ODBC installer keywords, tried in order. CREATE_DBV4 / CREATE_DBV12 ask the Access Database Engine for a
# Jet 4 .mdb / an .accdb explicitly; Windows' Jet driver only knows CREATE_DB.
_CREATE_KEYWORDS = {("jet", "mdb"): ("CREATE_DB",), ("ace", "mdb"): ("CREATE_DBV4", "CREATE_DB"),
                    ("ace", "accdb"): ("CREATE_DBV12", "CREATE_DB")}
# OLE DB providers that set the password: the same engine as the driver. ACE 16.0 if 12.0 isn't registered.
_PROVIDERS = {"jet": ("Microsoft.Jet.OLEDB.4.0",), "ace": ("Microsoft.ACE.OLEDB.12.0", "Microsoft.ACE.OLEDB.16.0")}


def engine(driver: str) -> str:
    """'ace' for the Access Database Engine's driver, 'jet' for Windows' built-in one."""
    return "ace" if "accdb" in driver.lower() else "jet"


def access_driver(kind: str = "auto", fmt: str = "mdb", installed: list[str] | None = None) -> str:
    """The exact name of the Access ODBC driver that builds the test company (busy-mcp then reads it with it too).

    ``auto`` takes the Access Database Engine if installed, else Jet; ``jet`` and ``ace`` insist on that driver,
    and ``.accdb`` needs ACE. Raises RuntimeError when the driver isn't installed, so the run fails instead of
    quietly testing something else.
    """
    if kind not in DRIVER_CHOICES or fmt not in FORMAT_CHOICES:
        raise ValueError(f"unknown driver choice {kind!r} or format {fmt!r}")
    if installed is None:
        import pyodbc

        pyodbc.pooling = False  # before the first ODBC call; pooling would keep files open (see below)
        installed = pyodbc.drivers()
    access = [d for d in installed if "access driver" in d.lower()]
    ace = [d for d in access if engine(d) == "ace"]
    jet = [d for d in access if engine(d) == "jet"]
    if fmt == "accdb" and kind == "jet":
        raise RuntimeError("--access-format accdb needs the Access Database Engine: Windows' Jet driver can't "
                           "read .accdb files. Use --access-driver ace.")
    if kind == "ace" or fmt == "accdb":
        pool, wanted = ace, "the Access Database Engine's ODBC driver 'Microsoft Access Driver (*.mdb, *.accdb)'"
    elif kind == "jet":
        pool, wanted = jet, "Windows' built-in Jet ODBC driver 'Microsoft Access Driver (*.mdb)'"
    else:
        pool, wanted = jet + ace, "a Microsoft Access ODBC driver"
    if not pool:
        bits = struct.calcsize("P") * 8
        raise RuntimeError(f"--access-driver {kind} --access-format {fmt} needs {wanted}, but this {bits}-bit "
                           f"Python doesn't see it. Its ODBC drivers: {installed}")
    return pool[-1]  # auto: the Access Database Engine if installed


def read_header(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read(21)


def describe_header(head: bytes) -> str:
    """The first 21 bytes of an Access file in hex, then what they say."""
    if len(head) < 21:
        return f"{head.hex(' ') or 'empty'} (too short for an Access file)"
    name = head[4:19].decode("ascii", "replace")
    return f"{head.hex(' ')} = {name!r}, version byte 0x{head[20]:02x} ({_VERSIONS.get(head[20], 'unknown')})"


def _check_format(path: str, fmt: str) -> str:
    head = read_header(path)
    if head[:4] != _MAGIC or head[4:19] != _ENGINE_NAMES[fmt]:
        raise RuntimeError(f"{path} should be an {fmt} file, but its header is {describe_header(head)}; expected "
                           f"00 01 00 00 then {_ENGINE_NAMES[fmt].decode()!r}")
    return describe_header(head)


def _create_empty(path: str, driver: str, fmt: str) -> str:
    """Create an empty Access file through the driver's setup API; returns the keyword that did it."""
    odbccp32 = ctypes.windll.odbccp32  # type: ignore[attr-defined]
    errors = []
    for keyword in _CREATE_KEYWORDS[(engine(driver), fmt)]:
        attributes = ctypes.create_unicode_buffer(f'{keyword}="{path}" General\0\0')
        if odbccp32.SQLConfigDataSourceW(None, 1, driver, attributes) and os.path.exists(path):  # 1 = ODBC_ADD_DSN
            return keyword
        code, message = ctypes.c_uint32(), ctypes.create_unicode_buffer(512)
        odbccp32.SQLInstallerErrorW(1, ctypes.byref(code), message, 512, None)
        errors.append(f"{keyword}: {message.value or 'no file was created'}")
    raise RuntimeError(f"Could not create {path} with '{driver}': {'; '.join(errors)}")


def _set_password(path: str, driver: str, required: bool) -> bool:
    """Protect the file with BUSY's password (ADO in exclusive mode, through the driver's own engine).

    False if that isn't possible here; with `required` that raises instead.
    """
    errors = []
    try:
        import win32com.client  # pywin32: installed with the MCP SDK on Windows
    except ImportError as exc:
        errors.append(f"pywin32: {exc}")
    else:
        for provider in _PROVIDERS[engine(driver)]:
            try:
                conn = win32com.client.Dispatch("ADODB.Connection")
                conn.Mode = 12  # adModeShareExclusive
                conn.Open(f"Provider={provider};Data Source={path};OLE DB Services=-2;")
            except Exception as exc:  # e.g. this provider isn't registered: try the next one
                errors.append(f"{provider}: {exc}")
                continue
            try:
                conn.Execute(f"ALTER DATABASE PASSWORD [{PASSWORD}] NULL")
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                break
            finally:
                conn.Close()
            print(f"Password set through ADO with {provider}")
            return True
    message = f"could not set the database password ({'; '.join(errors)})"
    if required:
        raise RuntimeError(message)
    print(f"WARNING: {message}; testing without one")
    return False


def _check_password_enforced(path: str, driver: str) -> None:
    """The driver must refuse the file without the password, or the password wasn't really set."""
    import pyodbc

    try:
        pyodbc.connect(f"DRIVER={{{driver}}};DBQ={path};", autocommit=True).close()
    except pyodbc.Error as exc:
        print(f"Without the password '{driver}' refuses the file, as it should: {exc}")
        return
    raise RuntimeError(f"'{driver}' opened {path} without a password, so it isn't protected")


def _value(value: Any, kind: str) -> Any:
    if kind == "DATETIME" and isinstance(value, str):
        return dt.datetime.fromisoformat(value)
    return value


def _copy_tables(sqlite_path: str, cursor: Any) -> None:
    src = sqlite3.connect(sqlite_path)
    try:
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        for table in tables:
            columns = [(r[1], (r[2] or "TEXT").upper()) for r in src.execute(f"PRAGMA table_info([{table}])")]
            kinds = ["DATETIME" if t == "TEXT" and _DATE_COLUMN.search(n) else _TYPES.get(t, "TEXT(255)")
                     for n, t in columns]
            cursor.execute(f"CREATE TABLE [{table}] ("
                           + ", ".join(f"[{n}] {k}" for (n, _), k in zip(columns, kinds, strict=True)) + ")")
            names = ", ".join(f"[{n}]" for n, _ in columns)
            for row in src.execute(f"SELECT * FROM [{table}]").fetchall():
                values = [_value(v, k) for v, k in zip(row, kinds, strict=True)]
                # NULL as a literal: the Access driver can't describe parameters, so a bound None may be mistyped.
                marks = ", ".join("NULL" if v is None else "?" for v in values)
                params = [v for v in values if v is not None]
                cursor.execute(f"INSERT INTO [{table}] ({names}) VALUES ({marks})", params)
    finally:
        src.close()


def build_access_company(sqlite_path: str, root: str, driver: str | None = None, fmt: str = "mdb",
                         require_password: bool = False) -> tuple[str, str]:
    """Copy the SQLite company into ``<root>\\DATA\\COMP0001\\db12024.bds``, an Access file in the `fmt` format
    built with `driver` (default: the ``auto`` choice).

    Returns the data folder and the password to open it with ("" if it couldn't be protected, which raises
    instead with `require_password`).
    """
    import pyodbc

    # Connection pooling would keep the file open after close() and block the final rename.
    pyodbc.pooling = False
    driver = driver or access_driver("auto", fmt)
    if (engine(driver), fmt) not in _CREATE_KEYWORDS:
        raise RuntimeError(f"'{driver}' can't create .{fmt} files; the Access Database Engine's driver can")
    company = os.path.join(root, "DATA", "COMP0001")
    os.makedirs(company, exist_ok=True)
    path = os.path.join(company, f"db12024.{fmt}")
    keyword = _create_empty(path, driver, fmt)
    print(f"Created {os.path.basename(path)} with '{driver}' ({keyword}); header {_check_format(path, fmt)}")
    password = PASSWORD if _set_password(path, driver, require_password) else ""
    if password and require_password:
        _check_password_enforced(path, driver)
    conn = pyodbc.connect(f"DRIVER={{{driver}}};DBQ={path};" + (f"PWD={password};" if password else ""),
                          autocommit=True)
    try:
        _copy_tables(sqlite_path, conn.cursor())
    finally:
        conn.close()
    bds = os.path.join(company, "db12024.bds")
    for attempt in range(10):  # the engine may release the file a moment after close()
        try:
            os.replace(path, bds)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.5)
    print(f"Access test company: {company} (driver '{driver}', format {fmt}, password "
          f"{'set' if password else 'NOT set'}; db12024.bds header {describe_header(read_header(bds))})")
    return os.path.join(root, "DATA"), password
