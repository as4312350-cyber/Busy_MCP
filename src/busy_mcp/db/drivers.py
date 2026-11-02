"""ODBC driver selection and connection-string building."""

from __future__ import annotations

import re
import struct
from typing import Any

from ..config import Settings
from ..errors import BusyConnectionError


def pyodbc_module() -> Any:
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - environment specific
        raise BusyConnectionError(
            "pyodbc could not be loaded. Install it with 'pip install pyodbc' "
            f"(and the system ODBC manager on Linux/macOS). Detail: {exc}"
        ) from exc
    return pyodbc


def brace(value: str) -> str:
    """Quote an ODBC attribute value so ';' and '}' inside it are safe."""
    return "{" + value.replace("}", "}}") + "}"


def access_value(value: str) -> str:
    """An attribute value for the Microsoft Access driver.

    Unlike the SQL Server drivers, the Access (Jet/ACE) driver keeps braces as part of the value, so
    ``PWD={ILoveMyINDIA}`` is rejected as "Not a valid password" (seen on a real password-protected .bds
    in CI). Braces are used only when a value can't be written without them.
    """
    if value != value.strip() or any(ch in value for ch in ";{}"):
        return brace(value)
    return value


def python_bits() -> int:
    return struct.calcsize("P") * 8


def pick_sql_driver(settings: Settings) -> str:
    if settings.sql_driver:
        return settings.sql_driver
    drivers = pyodbc_module().drivers()
    numbered = [(int(m.group(1)), d) for d in drivers if (m := re.match(r"ODBC Driver (\d+) for SQL Server", d))]
    if numbered:
        return max(numbered)[1]
    for fallback in ("SQL Server Native Client 11.0", "SQL Server"):
        if fallback in drivers:
            return fallback
    raise BusyConnectionError(
        "No SQL Server ODBC driver found. Install 'ODBC Driver 18 for SQL Server' from Microsoft."
    )


def pick_access_driver(settings: Settings) -> str:
    drivers = pyodbc_module().drivers()
    if settings.access_driver in drivers:
        return settings.access_driver
    for d in drivers:
        if "access driver" in d.lower():
            return d
    bits = python_bits()
    raise BusyConnectionError(
        f"No Microsoft Access ODBC driver is visible to this {bits}-bit Python. Install the "
        f"'Microsoft Access Database Engine 2016 Redistributable' ({'x64' if bits == 64 else 'x86'}), "
        "or use a Python whose bitness matches the Access engine already installed with BUSY "
        "(usually 32-bit)."
    )


def sqlserver_conn_str(settings: Settings, database: str) -> str:
    driver = pick_sql_driver(settings)
    modern = driver.startswith("ODBC Driver")
    parts = [
        f"DRIVER={brace(driver)}",
        f"SERVER={settings.sql_server}",
        f"DATABASE={brace(database)}",
        "APP=busy-mcp",
    ]
    if modern:
        parts.append("ApplicationIntent=ReadOnly")
    if settings.sql_user:
        parts += [f"UID={brace(settings.sql_user)}", f"PWD={brace(settings.sql_password or '')}"]
    else:
        parts.append("Trusted_Connection=yes")
    if settings.sql_trust_server_certificate:
        parts.append("TrustServerCertificate=yes")
    return ";".join(parts) + ";"


def access_conn_str(settings: Settings, path: str) -> str:
    parts = [
        f"DRIVER={brace(pick_access_driver(settings))}",
        f"DBQ={path}",
        "ReadOnly=1",
        "Exclusive=0",
    ]
    if settings.access_password:
        parts.append(f"PWD={access_value(settings.access_password)}")
    return ";".join(parts) + ";"
