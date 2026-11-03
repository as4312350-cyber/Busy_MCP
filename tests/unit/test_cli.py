"""setup wizard, doctor and client registration."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from busy_mcp.cli import main
from busy_mcp.cli.clients import register_claude_desktop, server_entry, snippets
from busy_mcp.cli.doctor import run_doctor
from busy_mcp.cli.wizard import EXIT_NEEDS_ACE, UI, run_setup
from busy_mcp.config import config_path, read_config_file
from busy_mcp.db import find_data_dirs, has_company_folders
from busy_mcp.errors import BusyMCPError


def _args(**kw):  # type: ignore[no-untyped-def]
    base = {"data_dir": None, "sql_server": None, "sql_user": None, "company": None, "backend": None,
            "database": None, "claude_config": None, "no_claude": False, "yes": True}
    base.update(kw)
    return Namespace(**base)


def test_setup_saves_config_and_registers_claude_with_backup(db_path, tmp_path):
    claude = tmp_path / "Claude" / "claude_desktop_config.json"
    claude.parent.mkdir()
    claude.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    lines: list[str] = []
    code = run_setup(_args(backend="sqlite", database=db_path, claude_config=str(claude)),
                     UI(assume_yes=True, out=lines.append))
    assert code == 0, lines
    saved = read_config_file()
    assert saved == {"backend": "sqlite", "database": db_path}
    data = json.loads(claude.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["busy"]["args"] == ["-m", "busy_mcp"]
    assert data["mcpServers"]["busy"]["env"] == {"BUSY_MCP_CONFIG": str(config_path())}
    assert list(claude.parent.glob("claude_desktop_config.json.bak-*"))
    text = "\n".join(lines)
    assert "Connection OK" in text and "Demo Traders" in text and "Claude Desktop configured" in text


def test_setup_reports_unreadable_data(tmp_path):
    lines: list[str] = []
    assert run_setup(_args(data_dir=str(tmp_path), no_claude=True), UI(True, out=lines.append)) == 1
    assert any("could not read BUSY" in x for x in lines)


def test_setup_interactive_choices(db_path, tmp_path):
    answers = iter(["n"])  # not SQL Server -> access path with typed folder (invalid) -> fails cleanly
    lines: list[str] = []
    ui = UI(assume_yes=False, input_fn=lambda _: next(answers, ""), out=lines.append)
    assert run_setup(_args(yes=False, no_claude=True), ui) == 1


def test_register_refuses_invalid_json(tmp_path):
    bad = tmp_path / "claude_desktop_config.json"
    bad.write_text("{oops")
    with pytest.raises(BusyMCPError, match="not valid JSON"):
        register_claude_desktop(bad)
    assert bad.read_text() == "{oops"


def test_register_creates_new_file(tmp_path):
    target = tmp_path / "new" / "claude_desktop_config.json"
    path, backup = register_claude_desktop(target)
    assert backup is None and json.loads(path.read_text())["mcpServers"]["busy"]["command"] == sys.executable


def test_snippets_and_entry():
    assert server_entry()["args"] == ["-m", "busy_mcp"]
    s = snippets()
    assert set(s) == {"Cursor (~/.cursor/mcp.json)", "VS Code (.vscode/mcp.json)", "Claude Code (terminal)"}
    assert json.loads(s["VS Code (.vscode/mcp.json)"])["servers"]["busy"]["type"] == "stdio"


def test_doctor_ok(monkeypatch, db_path):
    monkeypatch.setenv("BUSY_BACKEND", "sqlite")
    monkeypatch.setenv("BUSY_DATABASE", db_path)
    monkeypatch.setenv("BUSY_FY", "2024")
    lines: list[str] = []
    assert run_doctor(lines.append) == 0
    text = "\n".join(lines)
    assert "Trial balance tallies" in text and "Demo Traders" in text and "Fast-read index built" in text
    assert "bill_by_bill" in text


def test_doctor_reports_missing_data(monkeypatch, tmp_path):
    monkeypatch.setenv("BUSY_BACKEND", "access")
    monkeypatch.setenv("BUSY_DATA_DIR", str(tmp_path / "missing"))
    lines: list[str] = []
    assert run_doctor(lines.append) == 1
    assert any("[FAIL]" in x for x in lines)


def test_cli_entry_points(monkeypatch, db_path, capsys):
    monkeypatch.setenv("BUSY_BACKEND", "sqlite")
    monkeypatch.setenv("BUSY_DATABASE", db_path)
    for argv in (["doctor"], ["--check"]):
        with pytest.raises(SystemExit) as ei:
            main(argv)
        assert ei.value.code == 0
    assert "Result: ready" in capsys.readouterr().out
    monkeypatch.setenv("BUSY_FY", "nonsense")
    with pytest.raises(SystemExit) as ei:
        main(["doctor"])
    assert ei.value.code == 1


def test_data_folder_detection(tmp_path):
    good = tmp_path / "BusyWin" / "DATA" / "COMP0001"
    good.mkdir(parents=True)
    (good / "db12024.bds").write_bytes(b"")
    assert has_company_folders(str(good.parent))
    assert not has_company_folders(str(tmp_path))
    assert find_data_dirs([str(good.parent), str(tmp_path), str(Path("/nope"))]) == [str(good.parent)]


def test_setup_stores_absolute_paths(db_path, tmp_path, monkeypatch):
    """AI apps launch the server from another folder, so saved paths must be absolute."""
    import shutil

    shutil.copy(db_path, tmp_path / "db12024.sqlite")
    monkeypatch.chdir(tmp_path)
    args = _args(backend="sqlite", database="db12024.sqlite", no_claude=True)
    assert run_setup(args, UI(True, out=lambda _: None)) == 0
    assert read_config_file()["database"] == str(tmp_path / "db12024.sqlite")


def test_setup_asks_the_installer_for_ace_when_the_file_needs_it(monkeypatch, tmp_path):
    """Jet cannot open an .accdb file: setup exits with the code that makes install.cmd install ACE."""
    from busy_mcp.cli import wizard
    from busy_mcp.errors import BusyConnectionError

    (tmp_path / "COMP0001").mkdir()
    (tmp_path / "COMP0001" / "db12024.bds").write_bytes(b"x")

    def refuse(values, ui):  # type: ignore[no-untyped-def]
        raise BusyConnectionError("Cannot open COMP0001/2024: [HY000] Cannot open database '(unknown)'. It may not be "
                                  "a database that your application recognizes (-1028) (SQLDriverConnect)")

    monkeypatch.setattr(wizard, "_test", refuse)
    monkeypatch.setattr(wizard, "_ace_installed", lambda: False)
    lines: list[str] = []
    assert run_setup(_args(data_dir=str(tmp_path), no_claude=True), UI(True, out=lines.append)) == EXIT_NEEDS_ACE == 3
    assert any("not installed" in x for x in lines)

    # With ACE present the file itself is the problem: exit 1, so the installer doesn't loop installing ACE.
    monkeypatch.setattr(wizard, "_ace_installed", lambda: True)
    lines.clear()
    assert run_setup(_args(data_dir=str(tmp_path), no_claude=True), UI(True, out=lines.append)) == 1
    assert any("could not read this file" in x for x in lines)
