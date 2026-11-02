"""Runtime configuration, read from environment variables.

Every setting has a safe default so the server starts with zero config on a
typical single-PC BUSY install (Access edition, data in C:\\BusyWin\\DATA).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _env_list(name: str, default: str) -> list[str]:
    raw = _env(name, default) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass
class Settings:
    # "auto" | "access" | "sqlserver" | "sqlite" (sqlite is for tests/demos only)
    backend: str = "auto"

    # --- Access (.bds) edition ---------------------------------------------
    data_dir: str = r"C:\BusyWin\DATA"
    access_password: str = "ILoveMyINDIA"  # BUSY's well-known default .bds password
    access_driver: str = "Microsoft Access Driver (*.mdb, *.accdb)"

    # --- SQL Server edition -------------------------------------------------
    sql_server: str | None = None  # e.g. "localhost\\SQLEXPRESS" or "192.168.1.10,1433"
    sql_user: str | None = None  # leave empty for Windows (trusted) auth
    sql_password: str | None = None
    sql_driver: str | None = None  # auto-picks newest installed "ODBC Driver NN for SQL Server"
    sql_trust_server_certificate: bool = True
    sql_read_uncommitted: bool = False  # True = never block BUSY users, but may see half-saved vouchers

    # --- Target selection ---------------------------------------------------
    company: str | None = None  # e.g. "COMP0001"; default = first company found
    fy: int | None = None  # FY start year, e.g. 2024 for FY 2024-25; default = latest
    database: str | None = None  # explicit SQL db name or .bds file path (skips discovery)
    odbc_connection_string: str | None = None  # full override, used as-is

    # --- Accounting conventions (BUSY default group names) ------------------
    pl_groups: list[str] = field(default_factory=lambda: ["Revenue Accounts"])
    debtor_groups: list[str] = field(default_factory=lambda: ["Sundry Debtors"])
    creditor_groups: list[str] = field(default_factory=lambda: ["Sundry Creditors"])
    vch_type_names: dict[int, str] = field(default_factory=dict)
    item_inward_sign: int | None = None  # +1 / -1 ; None = auto-detect from purchase vouchers

    # --- Limits -------------------------------------------------------------
    max_rows: int = 1000
    query_timeout: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.backend = (_env("BUSY_BACKEND", s.backend) or "auto").lower()
        s.data_dir = _env("BUSY_DATA_DIR", s.data_dir) or s.data_dir
        s.access_password = _env("BUSY_ACCESS_PASSWORD", s.access_password) or ""
        s.access_driver = _env("BUSY_ACCESS_DRIVER", s.access_driver) or s.access_driver
        s.sql_server = _env("BUSY_SQL_SERVER")
        s.sql_user = _env("BUSY_SQL_USER")
        s.sql_password = _env("BUSY_SQL_PASSWORD")
        s.sql_driver = _env("BUSY_SQL_DRIVER")
        s.sql_trust_server_certificate = (_env("BUSY_SQL_TRUST_CERT", "1") or "1") not in ("0", "false", "no")
        s.sql_read_uncommitted = (_env("BUSY_SQL_NOLOCK", "0") or "0") in ("1", "true", "yes")
        s.company = _env("BUSY_COMPANY")
        fy = _env("BUSY_FY")
        s.fy = int(fy) if fy and fy.isdigit() else None
        s.database = _env("BUSY_DATABASE")
        s.odbc_connection_string = _env("BUSY_ODBC_CONNECTION_STRING")
        s.pl_groups = _env_list("BUSY_PL_GROUPS", ",".join(s.pl_groups))
        s.debtor_groups = _env_list("BUSY_DEBTOR_GROUPS", ",".join(s.debtor_groups))
        s.creditor_groups = _env_list("BUSY_CREDITOR_GROUPS", ",".join(s.creditor_groups))
        raw_map = _env("BUSY_VCH_TYPE_NAMES")
        if raw_map:
            try:
                s.vch_type_names = {int(k): str(v) for k, v in json.loads(raw_map).items()}
            except (ValueError, AttributeError):
                pass
        sign = _env("BUSY_ITEM_INWARD_SIGN")
        if sign in ("1", "+1"):
            s.item_inward_sign = 1
        elif sign == "-1":
            s.item_inward_sign = -1
        s.max_rows = _env_int("BUSY_MAX_ROWS", s.max_rows)
        s.query_timeout = _env_int("BUSY_QUERY_TIMEOUT", s.query_timeout)
        return s
