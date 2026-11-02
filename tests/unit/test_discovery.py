from __future__ import annotations

import pytest

from busy_mcp.config import Settings
from busy_mcp.db import access_conn_str, choose_target, discover_targets, drivers, sqlserver_conn_str
from busy_mcp.errors import BusyConnectionError


class FakePyodbc:
    @staticmethod
    def drivers() -> list[str]:
        return ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server",
                "Microsoft Access Driver (*.mdb, *.accdb)"]


class LegacyPyodbc:
    @staticmethod
    def drivers() -> list[str]:
        return ["SQL Server"]


def test_access_discovery(tmp_path):
    for comp, years in {"Comp0001": [2023, 2024], "COMP0002": [2024]}.items():
        d = tmp_path / comp
        d.mkdir()
        (d / "db.bds").write_bytes(b"")
        for y in years:
            (d / f"db1{y}.bds").write_bytes(b"")
    (tmp_path / "Backup").mkdir()
    (tmp_path / "COMP0003").mkdir()  # company folder without FY files
    targets = discover_targets(Settings(backend="access", data_dir=str(tmp_path)))
    assert [t.key for t in targets] == ["COMP0001/2024", "COMP0001/2023", "COMP0002/2024"]
    assert choose_target(targets, None, None).key == "COMP0001/2024"
    assert choose_target(targets, "comp0001", 2023).fy == 2023
    with pytest.raises(BusyConnectionError):
        choose_target(targets, "COMP0009", None)
    with pytest.raises(BusyConnectionError):
        choose_target(targets, "COMP0002", 2019)
    with pytest.raises(BusyConnectionError):
        choose_target([], None, None)


def test_missing_data_dir():
    with pytest.raises(BusyConnectionError, match="BUSY_DATA_DIR"):
        discover_targets(Settings(backend="access", data_dir="/definitely/not/here"))


def test_conn_strings_modern_driver(monkeypatch):
    monkeypatch.setattr(drivers, "pyodbc_module", lambda: FakePyodbc)
    s = Settings(sql_server=r"PC\SQLEXPRESS")
    cs = sqlserver_conn_str(s, "BusyCOMP0001_db12024")
    assert "ODBC Driver 18 for SQL Server" in cs
    assert "Trusted_Connection=yes" in cs and "ApplicationIntent=ReadOnly" in cs
    s2 = Settings(sql_server="10.0.0.5,1433", sql_user="busy_reader", sql_password="p;w}d")
    cs2 = sqlserver_conn_str(s2, "x")
    assert "UID={busy_reader}" in cs2 and "PWD={p;w}}d}" in cs2
    ac = access_conn_str(Settings(), r"C:\BusyWin\DATA\COMP0001\db12024.bds")
    assert "ReadOnly=1" in ac and "PWD={ILoveMyINDIA}" in ac


def test_conn_strings_legacy_driver_has_no_application_intent(monkeypatch):
    monkeypatch.setattr(drivers, "pyodbc_module", lambda: LegacyPyodbc)
    cs = sqlserver_conn_str(Settings(sql_server="x"), "db")
    assert "DRIVER={SQL Server}" in cs and "ApplicationIntent" not in cs
    with pytest.raises(BusyConnectionError, match="Access ODBC driver"):
        access_conn_str(Settings(), "x.bds")


def test_sqlserver_explicit_database():
    t = discover_targets(Settings(backend="sqlserver", sql_server="x", database="BusyComp0003_db12025"))[0]
    assert (t.company, t.fy) == ("COMP0003", 2025)


def test_settings_from_env_validation(monkeypatch):
    monkeypatch.setenv("BUSY_FY", "24")
    with pytest.raises(BusyConnectionError):
        Settings.from_env()
    monkeypatch.setenv("BUSY_FY", "2024")
    monkeypatch.setenv("BUSY_VCH_TYPE_NAMES", '{"99": "Custom"}')
    monkeypatch.setenv("BUSY_SQL_PASSWORD", "secret")
    monkeypatch.setenv("BUSY_MAX_ROWS", "999999999")
    s = Settings.from_env()
    assert s.fy == 2024 and s.vch_type_names == {99: "Custom"} and s.max_rows == 100_000
    view = s.public_view()
    assert view["sql_password"] == "***" and view["access_password"] == "***"
    monkeypatch.setenv("BUSY_BACKEND", "oracle")
    with pytest.raises(BusyConnectionError):
        Settings.from_env()
