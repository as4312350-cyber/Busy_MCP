"""The test company as a real Microsoft Access database, laid out like BUSY's (``COMP0001\\db12024.bds``).

Windows only. The Python must have the same bitness as an installed Access ODBC driver; Windows itself
ships the 32-bit Jet driver, so 32-bit Python always works. Like BUSY's own files, the copy is protected
with BUSY's default password, so the tests open it exactly the way busy-mcp opens a user's data.

Used by ``pytest --backend access`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import re
import sqlite3
import struct
from typing import Any

PASSWORD = "ILoveMyINDIA"  # BUSY's publicly known default .bds password
_DATE_COLUMN = re.compile(r"(Date|Time)$")  # BUSY keeps these as Access Date/Time; the SQLite copy as text
_TYPES = {"INTEGER": "LONG", "REAL": "DOUBLE", "TEXT": "TEXT(255)"}


def access_driver() -> str:
    import pyodbc

    drivers = [d for d in pyodbc.drivers() if "access driver" in d.lower()]
    if not drivers:
        bits = struct.calcsize("P") * 8
        raise RuntimeError(f"No Microsoft Access ODBC driver for this {bits}-bit Python; drivers: {pyodbc.drivers()}")
    return sorted(drivers, key=lambda d: "accdb" in d.lower())[-1]  # the Access Database Engine if installed


def _create_empty(path: str, driver: str) -> None:
    odbccp32 = ctypes.windll.odbccp32  # type: ignore[attr-defined]
    attributes = ctypes.create_unicode_buffer(f'CREATE_DB="{path}" General\0\0')
    if not odbccp32.SQLConfigDataSourceW(None, 1, driver, attributes):  # 1 = ODBC_ADD_DSN
        code, message = ctypes.c_uint32(), ctypes.create_unicode_buffer(512)
        odbccp32.SQLInstallerErrorW(1, ctypes.byref(code), message, 512, None)
        raise RuntimeError(f"Could not create {path} with '{driver}': {message.value}")


def _set_password(path: str) -> bool:
    """Protect the file with BUSY's password (Jet OLE DB in exclusive mode). False if that isn't possible here."""
    try:
        import win32com.client  # pywin32: installed with the MCP SDK on Windows

        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Mode = 12  # adModeShareExclusive
        conn.Open(f"Provider=Microsoft.Jet.OLEDB.4.0;Data Source={path};OLE DB Services=-2;")
        try:
            conn.Execute(f"ALTER DATABASE PASSWORD [{PASSWORD}] NULL")
        finally:
            conn.Close()
        return True
    except Exception as exc:
        print(f"WARNING: could not set the database password ({exc}); testing without one")
        return False


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


def build_access_company(sqlite_path: str, root: str) -> tuple[str, str]:
    """Copy the SQLite company into ``<root>\\DATA\\COMP0001\\db12024.bds``.

    Returns the data folder and the password to open it with ("" if it couldn't be protected).
    """
    import pyodbc

    # Connection pooling would keep the file open after close() and block the final rename.
    pyodbc.pooling = False
    driver = access_driver()
    company = os.path.join(root, "DATA", "COMP0001")
    os.makedirs(company, exist_ok=True)
    mdb = os.path.join(company, "db12024.mdb")
    _create_empty(mdb, driver)
    password = PASSWORD if _set_password(mdb) else ""
    conn = pyodbc.connect(f"DRIVER={{{driver}}};DBQ={mdb};" + (f"PWD={password};" if password else ""),
                          autocommit=True)
    try:
        _copy_tables(sqlite_path, conn.cursor())
    finally:
        conn.close()
    os.replace(mdb, os.path.join(company, "db12024.bds"))
    print(f"Access test company: {company} (driver '{driver}', password {'set' if password else 'NOT set'})")
    return os.path.join(root, "DATA"), password
