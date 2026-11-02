from __future__ import annotations

import json

import pytest

from busy_mcp.config import Settings, config_path, load_settings, read_config_file, settings_from, write_config_file
from busy_mcp.errors import BusyConnectionError


def test_defaults_need_no_configuration():
    s = load_settings(environ={})
    assert s.backend == "auto" and s.data_dir.endswith("DATA") and s.denied_tables == ["Config"]
    assert s.origin == {}


def test_file_then_environment_precedence(tmp_path):
    path = tmp_path / "config.json"
    write_config_file({"data_dir": r"D:\Busy\DATA", "company": "COMP0002", "max_rows": 50}, path)
    s = load_settings(environ={"BUSY_COMPANY": "COMP0009"}, path=path)
    assert s.data_dir == r"D:\Busy\DATA" and s.max_rows == 50
    assert s.company == "COMP0009"
    assert s.origin == {"data_dir": "file", "company": "env", "max_rows": "file"}


def test_config_file_validation(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"no_such_setting": 1}))
    with pytest.raises(BusyConnectionError, match="Unknown setting"):
        read_config_file(bad)
    bad.write_text("{not json")
    with pytest.raises(BusyConnectionError, match="Cannot read"):
        read_config_file(bad)
    with pytest.raises(BusyConnectionError):
        write_config_file({"fy": "24"}, tmp_path / "x.json")


@pytest.mark.parametrize(("env", "message"), [
    ({"BUSY_FY": "24"}, "4-digit"),
    ({"BUSY_BACKEND": "oracle"}, "must be one of"),
    ({"BUSY_VCH_TYPE_NAMES": "not json"}, "JSON"),
    ({"BUSY_ITEM_INWARD_SIGN": "2"}, "1 or -1"),
    ({"BUSY_PREFETCH": "maybe"}, "true/false"),
    ({"BUSY_ITEM_RECTYPES": "2,x"}, "numbers"),
])
def test_invalid_environment_values(env, message):
    with pytest.raises(BusyConnectionError, match=message):
        load_settings(environ=env)


def test_parsing_and_masking():
    dummy_password = "not-a-real-password"  # pragma: allowlist secret
    s = load_settings(environ={"BUSY_VCH_TYPE_NAMES": '{"99": "Custom"}', "BUSY_SQL_PASSWORD": dummy_password,
                               "BUSY_MAX_ROWS": "999999999", "BUSY_PL_GROUPS": "Income, Expenses",
                               "BUSY_SQL_NOLOCK": "yes", "BUSY_ITEM_RECTYPES": "2, 20"})
    assert s.vch_type_names == {99: "Custom"} and s.max_rows == 100_000
    assert s.pl_groups == ["Income", "Expenses"] and s.sql_read_uncommitted is True
    assert s.item_rectypes == [2, 20]
    view = s.public_view()
    assert view["sql_password"] == "***" and view["access_password"] == "***" and "origin" not in view


def test_settings_from_and_config_path(monkeypatch, tmp_path):
    assert settings_from({"backend": "sqlite", "fy": 2024}).fy == 2024
    monkeypatch.setenv("BUSY_MCP_CONFIG", str(tmp_path / "c.json"))
    assert config_path() == tmp_path / "c.json"
    assert isinstance(Settings.from_env(), Settings)
