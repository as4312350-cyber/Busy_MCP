from __future__ import annotations

import os

import pytest

from busy_mcp.config import Settings
from busy_mcp.db import (
    access_conn_str,
    choose_target,
    data_dir_rank,
    discover_targets,
    drivers,
    open_companies,
    scan_for_company_folders,
    sqlserver_conn_str,
)
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
    # The Access driver keeps braces as part of the password (verified against real Jet in CI): none here.
    assert "ReadOnly=1" in ac and "PWD=ILoveMyINDIA;" in ac
    assert "PWD={p;w}" in access_conn_str(Settings(access_password="p;w"), "x.bds")


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


def _company(root, name, *, open_now=False, saved=1000.0, comp="COMP0001"):  # type: ignore[no-untyped-def]
    comp_dir = root / name / comp
    comp_dir.mkdir(parents=True)
    bds = comp_dir / "db12024.bds"
    bds.write_bytes(b"x")
    os.utime(bds, (saved, saved))
    if open_now:
        (comp_dir / "db12024.ldb").write_bytes(b"")
    return root / name


def test_scan_finds_data_folders_a_few_levels_down(tmp_path):
    data = _company(tmp_path / "Apps" / "Accounts", "DATA")
    (tmp_path / "Windows" / "System32").mkdir(parents=True)
    assert scan_for_company_folders([str(tmp_path)]) == [str(data)]
    assert scan_for_company_folders([str(tmp_path)], max_depth=1) == []  # too deep for this limit


def test_scan_survives_missing_and_unreadable_roots(tmp_path):
    assert scan_for_company_folders([str(tmp_path / "nope")]) == []


def test_data_folder_with_a_company_open_in_busy_comes_first(tmp_path):
    old = _company(tmp_path, "OldData", saved=9999.0)  # saved more recently, but nobody has it open
    live = _company(tmp_path, "LiveData", open_now=True, saved=1000.0)
    assert open_companies(str(live)) == ["COMP0001"] and open_companies(str(old)) == []
    assert sorted([str(old), str(live)], key=data_dir_rank) == [str(live), str(old)]


def test_without_an_open_company_the_most_recently_saved_folder_wins(tmp_path):
    a = _company(tmp_path, "A", saved=1000.0)
    b = _company(tmp_path, "B", saved=5000.0)
    assert sorted([str(a), str(b)], key=data_dir_rank) == [str(b), str(a)]
