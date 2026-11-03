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


def is_access_driver(name: str) -> bool:
    """An ODBC driver for Access files: Windows' built-in Jet driver or the Access Database Engine's."""
    return "access driver" in name.lower()


def is_ace_driver(name: str) -> bool:
    """The Access Database Engine (ACE) driver. It reads .mdb and .accdb files; Windows' Jet driver reads .mdb only."""
    return is_access_driver(name) and "accdb" in name.lower()


def access_drivers(installed: list[str]) -> list[str]:
    """The Access ODBC drivers among `installed`, the Access Database Engine first."""
    return sorted((d for d in installed if is_access_driver(d)), key=lambda d: not is_ace_driver(d))


# Every Access file since Access 97 starts with 00 01 00 00 and a 15-byte engine name; byte 0x14 is the version.
_ACCESS_MAGIC = b"\x00\x01\x00\x00"
_ACCESS_ENGINES = {b"Standard Jet DB": "mdb", b"Standard ACE DB": "accdb"}
_ACCESS_VERSIONS = {0: "Jet 3, Access 97", 1: "Jet 4, Access 2000-2003", 2: "ACE 12, Access 2007",
                    3: "ACE 14, Access 2010", 5: "ACE 16", 6: "ACE 17"}


def access_file_format(path: str) -> tuple[str, str]:
    """The Access format of a BUSY file, read from its first 21 bytes: ("mdb" | "accdb" | "", description).

    .mdb files ("Standard Jet DB") open with Windows' Jet driver and with the Access Database Engine; .accdb files
    ("Standard ACE DB", Access 2007 and later) only with the Access Database Engine. Never raises.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(21)
    except OSError as exc:
        return "", f"unknown (cannot read the file: {exc.strerror or exc})"
    kind = _ACCESS_ENGINES.get(head[4:19], "") if len(head) == 21 and head[:4] == _ACCESS_MAGIC else ""
    if not kind:
        return "", f"not an Access database (first bytes: {head.hex(' ') or 'none'})"
    version = _ACCESS_VERSIONS.get(head[20], f"version byte 0x{head[20]:02x}")
    if kind == "mdb":
        return kind, f".mdb ({version}); Windows' Jet driver and the Access Database Engine can both read it"
    return kind, f".accdb ({version}); only the Microsoft Access Database Engine (ACE) can read it"


def pick_access_driver(settings: Settings) -> str:
    drivers = pyodbc_module().drivers()
    if settings.access_driver in drivers:
        return settings.access_driver
    found = access_drivers(drivers)
    if found:
        return found[0]
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
