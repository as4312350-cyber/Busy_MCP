"""Runtime configuration, read from environment variables.

Every setting has a safe default so the server starts with zero config on a
typical single-PC BUSY install (Access edition, data in ``C:\\BusyWin\\DATA``).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from .errors import BusyConnectionError

BACKENDS = ("auto", "access", "sqlserver", "odbc", "sqlite")
_TRUE = ("1", "true", "yes", "on")


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if raw is None:
        return list(default)
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        val = int(raw)
    except ValueError as exc:
        raise BusyConnectionError(f"{name} must be an integer, got {raw!r}") from exc
    return max(lo, min(hi, val))


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    return default if raw is None else raw.lower() in _TRUE


@dataclass
class Settings:
    """All tunables. Secrets are excluded from :meth:`public_view`."""

    backend: str = "auto"

    # Access (.bds) edition
    data_dir: str = r"C:\BusyWin\DATA"
    access_password: str = "ILoveMyINDIA"  # noqa: S105 - BUSY's well-known default .bds password
    access_driver: str = "Microsoft Access Driver (*.mdb, *.accdb)"

    # SQL Server edition
    sql_server: str | None = None
    sql_user: str | None = None
    sql_password: str | None = None
    sql_driver: str | None = None
    sql_trust_server_certificate: bool = True
    sql_read_uncommitted: bool = False

    # Target selection
    company: str | None = None
    fy: int | None = None
    database: str | None = None
    odbc_connection_string: str | None = None

    # Accounting conventions
    pl_groups: list[str] = field(default_factory=lambda: ["Revenue Accounts"])
    debtor_groups: list[str] = field(default_factory=lambda: ["Sundry Debtors"])
    creditor_groups: list[str] = field(default_factory=lambda: ["Sundry Creditors"])
    vch_type_names: dict[int, str] = field(default_factory=dict)
    item_inward_sign: int | None = None
    include_cancelled: bool = False

    # Limits & behaviour
    max_rows: int = 1000
    query_timeout: int = 60
    cache_ttl: int = 300
    audit_queries: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        s = cls()
        s.backend = (_env("BUSY_BACKEND", s.backend) or "auto").lower()
        if s.backend not in BACKENDS:
            raise BusyConnectionError(f"BUSY_BACKEND must be one of {BACKENDS}, got {s.backend!r}")
        s.data_dir = _env("BUSY_DATA_DIR", s.data_dir) or s.data_dir
        s.access_password = _env("BUSY_ACCESS_PASSWORD", s.access_password) or ""
        s.access_driver = _env("BUSY_ACCESS_DRIVER", s.access_driver) or s.access_driver
        s.sql_server = _env("BUSY_SQL_SERVER")
        s.sql_user = _env("BUSY_SQL_USER")
        s.sql_password = _env("BUSY_SQL_PASSWORD")
        s.sql_driver = _env("BUSY_SQL_DRIVER")
        s.sql_trust_server_certificate = _env_bool("BUSY_SQL_TRUST_CERT", True)
        s.sql_read_uncommitted = _env_bool("BUSY_SQL_NOLOCK", False)
        s.company = _env("BUSY_COMPANY")
        fy = _env("BUSY_FY")
        if fy is not None:
            if not (fy.isdigit() and 1990 <= int(fy) <= 2100):
                raise BusyConnectionError(f"BUSY_FY must be a 4-digit FY start year like 2024, got {fy!r}")
            s.fy = int(fy)
        s.database = _env("BUSY_DATABASE")
        s.odbc_connection_string = _env("BUSY_ODBC_CONNECTION_STRING")
        s.pl_groups = _env_list("BUSY_PL_GROUPS", s.pl_groups)
        s.debtor_groups = _env_list("BUSY_DEBTOR_GROUPS", s.debtor_groups)
        s.creditor_groups = _env_list("BUSY_CREDITOR_GROUPS", s.creditor_groups)
        raw_map = _env("BUSY_VCH_TYPE_NAMES")
        if raw_map:
            try:
                s.vch_type_names = {int(k): str(v) for k, v in json.loads(raw_map).items()}
            except (ValueError, AttributeError) as exc:
                raise BusyConnectionError(
                    'BUSY_VCH_TYPE_NAMES must be JSON like {"14": "Receipt"}'
                ) from exc
        sign = _env("BUSY_ITEM_INWARD_SIGN")
        if sign is not None:
            if sign not in ("1", "+1", "-1"):
                raise BusyConnectionError("BUSY_ITEM_INWARD_SIGN must be 1 or -1")
            s.item_inward_sign = -1 if sign == "-1" else 1
        s.include_cancelled = _env_bool("BUSY_INCLUDE_CANCELLED", False)
        s.max_rows = _env_int("BUSY_MAX_ROWS", s.max_rows, 1, 100_000)
        s.query_timeout = _env_int("BUSY_QUERY_TIMEOUT", s.query_timeout, 1, 3600)
        s.cache_ttl = _env_int("BUSY_CACHE_TTL", s.cache_ttl, 0, 86_400)
        s.audit_queries = _env_bool("BUSY_AUDIT_QUERIES", True)
        return s

    def public_view(self) -> dict[str, Any]:
        """Settings with secrets removed, safe to return to the client."""
        d = asdict(self)
        for secret in ("access_password", "sql_password", "odbc_connection_string"):
            if d.get(secret):
                d[secret] = "***"
        return d
