"""Runtime configuration.

Values are resolved in this order (later wins):

1. built-in defaults (a typical single-PC BUSY install needs nothing else),
2. the JSON config file written by ``busy-mcp setup``
   (``%APPDATA%\\busy-mcp\\config.json`` on Windows, ``~/.config/busy-mcp/config.json``
   elsewhere, or the path in ``BUSY_MCP_CONFIG``),
3. ``BUSY_*`` environment variables (e.g. set in an MCP client's ``env`` block).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .errors import BusyConnectionError

BACKENDS = ("auto", "access", "sqlserver", "odbc", "sqlite")
SECRET_FIELDS = ("access_password", "sql_password", "odbc_connection_string")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


@dataclass
class Settings:
    """Every tunable. Field names double as config-file keys."""

    backend: str = "auto"

    # Access (.bds) edition
    data_dir: str = r"C:\BusyWin\DATA"
    access_password: str = "ILoveMyINDIA"  # noqa: S105 - BUSY's publicly known default .bds password
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

    # Accounting conventions (BUSY's default group names)
    pl_groups: list[str] = field(default_factory=lambda: ["Revenue Accounts"])
    debtor_groups: list[str] = field(default_factory=lambda: ["Sundry Debtors"])
    creditor_groups: list[str] = field(default_factory=lambda: ["Sundry Creditors"])
    cash_bank_groups: list[str] = field(default_factory=lambda: ["Bank Accounts", "Cash-in-hand", "Bank O/D Account"])
    tax_groups: list[str] = field(default_factory=lambda: ["Duties & Taxes"])
    vch_type_names: dict[int, str] = field(default_factory=dict)
    item_inward_sign: int | None = None
    item_rectypes: list[int] | None = None
    include_cancelled: bool = False

    # Security
    denied_tables: list[str] = field(default_factory=lambda: ["Config"])
    allow_sensitive: bool = False

    # Performance & limits
    max_rows: int = 1000
    query_timeout: int = 60
    freshness_seconds: int = 10
    cache_ttl: int = 900
    prefetch: bool = True
    audit_queries: bool = True

    # Where each non-default value came from ("file", "env"); not a setting itself.
    origin: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> Settings:
        """Load defaults + config file + environment (kept for backwards compatibility)."""
        return load_settings()

    def public_view(self) -> dict[str, Any]:
        """Settings with secrets masked, safe to show to the client."""
        d = asdict(self)
        d.pop("origin", None)
        for secret in SECRET_FIELDS:
            if d.get(secret):
                d[secret] = "***"
        return d


# --------------------------------------------------------------------------- parsing


def _parse_bool(name: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise BusyConnectionError(f"{name} must be true/false, got {raw!r}")


def _parse_int(lo: int, hi: int) -> Callable[[str, Any], int]:
    def parse(name: str, raw: Any) -> int:
        try:
            val = int(raw)
        except (TypeError, ValueError) as exc:
            raise BusyConnectionError(f"{name} must be a whole number, got {raw!r}") from exc
        return max(lo, min(hi, val))

    return parse


def _parse_str(name: str, raw: Any) -> str | None:
    text = str(raw).strip()
    return text or None


def _parse_list(name: str, raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _parse_int_list(name: str, raw: Any) -> list[int] | None:
    items = _parse_list(name, raw)
    if not items:
        return None
    try:
        return [int(x) for x in items]
    except ValueError as exc:
        raise BusyConnectionError(f"{name} must be a list of numbers like 2,20") from exc


def _parse_backend(name: str, raw: Any) -> str:
    val = str(raw).strip().lower()
    if val not in BACKENDS:
        raise BusyConnectionError(f"{name} must be one of {', '.join(BACKENDS)}; got {raw!r}")
    return val


def _parse_fy(name: str, raw: Any) -> int:
    text = str(raw).strip()
    if not (text.isdigit() and 1990 <= int(text) <= 2100):
        raise BusyConnectionError(f"{name} must be a 4-digit FY start year like 2024, got {raw!r}")
    return int(text)


def _parse_sign(name: str, raw: Any) -> int:
    text = str(raw).strip()
    if text not in ("1", "+1", "-1"):
        raise BusyConnectionError(f"{name} must be 1 or -1")
    return -1 if text == "-1" else 1


def _parse_vch_names(name: str, raw: Any) -> dict[int, str]:
    try:
        data = raw if isinstance(raw, Mapping) else json.loads(str(raw))
        return {int(k): str(v) for k, v in data.items()}
    except (ValueError, AttributeError, TypeError) as exc:
        raise BusyConnectionError(f'{name} must be JSON like {{"14": "Receipt"}}') from exc


_PARSERS: dict[str, Callable[[str, Any], Any]] = {
    "backend": _parse_backend,
    "data_dir": _parse_str, "access_password": lambda n, r: str(r), "access_driver": _parse_str,
    "sql_server": _parse_str, "sql_user": _parse_str, "sql_password": lambda n, r: str(r), "sql_driver": _parse_str,
    "sql_trust_server_certificate": _parse_bool, "sql_read_uncommitted": _parse_bool,
    "company": _parse_str, "fy": _parse_fy, "database": _parse_str, "odbc_connection_string": _parse_str,
    "pl_groups": _parse_list, "debtor_groups": _parse_list, "creditor_groups": _parse_list,
    "cash_bank_groups": _parse_list, "tax_groups": _parse_list, "vch_type_names": _parse_vch_names,
    "item_inward_sign": _parse_sign, "item_rectypes": _parse_int_list, "include_cancelled": _parse_bool,
    "denied_tables": _parse_list, "allow_sensitive": _parse_bool,
    "max_rows": _parse_int(1, 100_000), "query_timeout": _parse_int(1, 3600),
    "freshness_seconds": _parse_int(0, 3600), "cache_ttl": _parse_int(0, 86_400),
    "prefetch": _parse_bool, "audit_queries": _parse_bool,
}

# Environment variable names that differ from BUSY_<FIELD>
_ENV_ALIASES = {
    "sql_trust_server_certificate": "BUSY_SQL_TRUST_CERT",
    "sql_read_uncommitted": "BUSY_SQL_NOLOCK",
}


def env_name(key: str) -> str:
    return _ENV_ALIASES.get(key, f"BUSY_{key.upper()}")


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "busy-mcp"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "busy-mcp"


def config_path() -> Path:
    override = os.environ.get("BUSY_MCP_CONFIG")
    return Path(override) if override else config_dir() / "config.json"


def read_config_file(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BusyConnectionError(f"Cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BusyConnectionError(f"Config file {path} must contain a JSON object")
    unknown = sorted(set(data) - set(_PARSERS))
    if unknown:
        raise BusyConnectionError(f"Unknown setting(s) {unknown} in {path}. Valid: {sorted(_PARSERS)}")
    return data


def write_config_file(values: Mapping[str, Any], path: Path | None = None) -> Path:
    """Validate and save settings (used by ``busy-mcp setup``)."""
    path = path or config_path()
    for key, raw in values.items():
        if key not in _PARSERS:
            raise BusyConnectionError(f"Unknown setting {key!r}")
        if raw is not None:
            _PARSERS[key](key, raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in values.items() if v is not None}
    path.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)  # owner-only on POSIX; harmless on Windows
    return path


def load_settings(environ: Mapping[str, str] | None = None, path: Path | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    settings = Settings()
    file_values = read_config_file(path)
    for key, raw in file_values.items():
        if raw is not None:
            setattr(settings, key, _PARSERS[key](key, raw))
            settings.origin[key] = "file"
    for f in fields(Settings):
        if f.name == "origin":
            continue
        raw = environ.get(env_name(f.name))
        if raw is None or raw.strip() == "":
            continue
        setattr(settings, f.name, _PARSERS[f.name](env_name(f.name), raw.strip()))
        settings.origin[f.name] = "env"
    return settings


def settings_from(values: Mapping[str, Any]) -> Settings:
    """Settings built from defaults plus `values` only (no file, no environment)."""
    settings = Settings()
    for key, raw in values.items():
        if key not in _PARSERS:
            raise BusyConnectionError(f"Unknown setting {key!r}")
        if raw is not None:
            setattr(settings, key, _PARSERS[key](key, raw))
    return settings
