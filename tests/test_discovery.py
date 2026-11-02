import pytest

from busy_mcp import db as dbmod
from busy_mcp.config import Settings
from busy_mcp.db import BusyConnectionError, access_conn_str, choose_target, discover_targets, sqlserver_conn_str


class FakePyodbc:
    @staticmethod
    def drivers():
        return ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server",
                "Microsoft Access Driver (*.mdb, *.accdb)"]


def test_access_discovery(tmp_path):
    for comp, years in {"Comp0001": [2023, 2024], "COMP0002": [2024]}.items():
        d = tmp_path / comp
        d.mkdir()
        (d / "db.bds").write_bytes(b"")
        for y in years:
            (d / f"db1{y}.bds").write_bytes(b"")
    (tmp_path / "Backup").mkdir()
    targets = discover_targets(Settings(backend="access", data_dir=str(tmp_path)))
    assert [t.key for t in targets] == ["COMP0001/2024", "COMP0001/2023", "COMP0002/2024"]
    assert choose_target(targets, None, None).key == "COMP0001/2024"
    assert choose_target(targets, "comp0001", 2023).fy == 2023
    with pytest.raises(BusyConnectionError):
        choose_target(targets, "COMP0009", None)
    with pytest.raises(BusyConnectionError):
        choose_target(targets, "COMP0002", 2019)


def test_conn_strings(monkeypatch):
    monkeypatch.setattr(dbmod, "_pyodbc", lambda: FakePyodbc)
    s = Settings(sql_server=r"PC\SQLEXPRESS")
    cs = sqlserver_conn_str(s, "BusyCOMP0001_db12024")
    assert "ODBC Driver 18 for SQL Server" in cs and "Trusted_Connection=yes" in cs
    s2 = Settings(sql_server="10.0.0.5,1433", sql_user="busy_reader", sql_password="p;w}d")
    cs2 = sqlserver_conn_str(s2, "x")
    assert "UID={busy_reader}" in cs2 and "PWD={p;w}}d}" in cs2
    ac = access_conn_str(Settings(), r"C:\BusyWin\DATA\COMP0001\db12024.bds")
    assert "ReadOnly=1" in ac and "PWD={ILoveMyINDIA}" in ac


def test_sqlserver_explicit_database():
    t = discover_targets(Settings(backend="sqlserver", sql_server="x", database="BusyComp0003_db12025"))[0]
    assert (t.company, t.fy) == ("COMP0003", 2025)
