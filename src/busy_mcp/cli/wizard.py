"""``busy-mcp setup``: a guided, non-technical setup.

It finds BUSY's data, tests that it can be read, saves the settings, and adds
busy-mcp to Claude Desktop (backing up the existing configuration first).
Nothing in BUSY is ever changed.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings, read_config_file, settings_from, write_config_file
from ..db import BusyDB, choose_target, data_folder_problem, discover_targets, find_data_dirs
from ..db.connection import is_unrecognized_format
from ..errors import BusyMCPError
from .clients import claude_desktop_config_path, register_claude_desktop, snippets

# Exit code of `busy-mcp setup` when BUSY's file needs the Microsoft Access Database Engine (ACE) that this PC
# lacks. scripts/install.ps1 reacts to it by installing ACE from Microsoft and running setup again.
EXIT_NEEDS_ACE = 3

# Exit code of `busy-mcp setup` when the BUSY data folder could not be found or opened. busy-mcp itself is fine;
# scripts/install.ps1 asks the person for the folder and runs setup again.
EXIT_NO_DATA = 4


class NoDataFolder(BusyMCPError):
    """The BUSY data folder is missing, empty or unreachable."""


def _ace_installed() -> bool:
    """True if an Access Database Engine (ACE) ODBC driver is visible to this Python."""
    try:
        from ..db.drivers import is_ace_driver, pyodbc_module

        return any(is_ace_driver(d) for d in pyodbc_module().drivers())
    except Exception:  # noqa: BLE001 - no pyodbc or no driver manager: treat as not installed
        return False


class UI:
    def __init__(self, assume_yes: bool, input_fn: Callable[[str], str] = input, out: Callable[[str], Any] = print,
                 secret_fn: Callable[[str], str] = getpass.getpass) -> None:
        self.yes = assume_yes
        self.input = input_fn
        self.out = out
        self.secret = secret_fn

    def say(self, text: str = "") -> None:
        self.out(text)

    def ask(self, question: str, default: str = "") -> str:
        if self.yes:
            return default
        answer = self.input(f"{question}{f' [{default}]' if default else ''}: ").strip()
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.yes:
            return default
        answer = self.input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    def choose(self, question: str, options: list[str]) -> int:
        if self.yes or len(options) == 1:
            return 0
        for i, opt in enumerate(options, 1):
            self.out(f"  {i}. {opt}")
        while True:
            answer = self.input(f"{question} [1]: ").strip() or "1"
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            self.out(f"Please type a number from 1 to {len(options)}.")


def _access_values(args: Any, ui: UI) -> dict[str, Any]:
    if args.data_dir:
        folder = args.data_dir
    else:
        found = find_data_dirs([Settings().data_dir])
        if found:
            ui.say("Found BUSY data in:")
            options = [*found, "Another folder"]
            pick = ui.choose("Which one should be used?", options)
            folder = found[pick] if pick < len(found) else ui.ask("Full path of the BUSY data folder")
        else:
            ui.say("BUSY data was not found automatically.")
            ui.say("In BUSY, open Company > 'Set Data Directory' to see the folder (e.g. C:\\BusyWin\\DATA, or "
                   "\\\\SERVER\\BusyWin\\DATA when the data is on another computer).")
            folder = ui.ask("Full path of the BUSY data folder", Settings().data_dir)
    folder = os.path.abspath(folder.strip().strip('"'))  # AI apps start the server from another folder
    problem = data_folder_problem(folder)  # says why, e.g. "The network path was not found"
    if problem:
        raise NoDataFolder(problem)
    return {"backend": "access", "data_dir": folder}


def _sql_values(args: Any, ui: UI) -> dict[str, Any]:
    server = args.sql_server or ui.ask("SQL Server instance (as in BUSY's login screen)", "localhost\\SQLEXPRESS")
    user = args.sql_user if args.sql_user is not None else (
        None if ui.yes else (ui.ask("SQL login name (leave empty to use Windows login)") or None))
    values: dict[str, Any] = {"backend": "sqlserver", "sql_server": server, "sql_user": user}
    if user:
        password = os.environ.get("BUSY_SQL_PASSWORD") or (None if ui.yes else ui.secret("SQL password: "))
        if not password:
            raise BusyMCPError("A password is needed for SQL login (or set BUSY_SQL_PASSWORD).")
        values["sql_password"] = password
    return values


def _test(values: dict[str, Any], ui: UI) -> tuple[Settings, list[Any]]:
    settings = settings_from(values)
    targets = discover_targets(settings)
    if not targets:
        raise BusyMCPError("No BUSY company databases were found there.")
    target = choose_target(targets, None, None)
    db = BusyDB(target, settings)
    try:
        from .. import reports

        ov = reports.company_overview(db)
    finally:
        db.close()
    name = (ov.get("company") or {}).get("name") or target.company
    ui.say(f"Connection OK: {len(targets)} company/year database(s); latest is {target.key} ({name}) "
           f"with {ov['vouchers'].get('n')} vouchers.")
    return settings, targets


def run_setup(args: Any, ui: UI | None = None) -> int:
    ui = ui or UI(assume_yes=bool(getattr(args, "yes", False)))
    ui.say("BUSY MCP setup. This only READS your BUSY data; nothing in BUSY is changed.\n")
    try:
        if getattr(args, "backend", None) == "sqlite":  # used by tests and demos
            values: dict[str, Any] = {"backend": "sqlite", "database": os.path.abspath(args.database)}
        elif args.sql_server:
            values = _sql_values(args, ui)
        elif args.data_dir:
            values = _access_values(args, ui)
        elif ui.confirm("Does your BUSY use SQL Server (BUSY SQL edition)?", default=False):
            values = _sql_values(args, ui)
        else:
            values = _access_values(args, ui)
        _, targets = _test(values, ui)
    except NoDataFolder as exc:
        ui.say(f"\nSetup could not find your BUSY data: {exc}")
        ui.say("In BUSY, open Company > 'Set Data Directory' to see the folder, then run setup again with it:")
        ui.say("  busy-mcp setup --data-dir \"C:\\path\\to\\BUSY\\DATA\"")
        ui.say("(SQL Server edition: use  --sql-server HOST\\INSTANCE  instead.)")
        return EXIT_NO_DATA
    except BusyMCPError as exc:
        ui.say(f"\nSetup could not read BUSY: {exc}")
        if is_unrecognized_format(str(exc)):
            if _ace_installed():
                ui.say("The Microsoft Access Database Engine is installed, but could not read this file. It may be "
                       "damaged, or newer than the engine. Check that BUSY itself opens this company.")
            else:
                ui.say("This BUSY file needs the Microsoft Access Database Engine (ACE), "
                       "which is not installed for this Python.")
                return EXIT_NEEDS_ACE
        ui.say("Run 'busy-mcp doctor' for a detailed diagnosis.")
        return 1

    companies = sorted({t.company for t in targets if t.company})
    if args.company:
        values["company"] = args.company
    elif len(companies) > 1:
        values["company"] = companies[ui.choose("Which company should be the default?", companies)]

    merged = {**read_config_file(), **values}
    saved = write_config_file(merged)
    ui.say(f"Settings saved to {saved}")

    if not getattr(args, "no_claude", False):
        claude_path = Path(args.claude_config) if getattr(args, "claude_config", None) else claude_desktop_config_path()
        if claude_path.parent.exists() or getattr(args, "claude_config", None):
            if ui.confirm("Add BUSY to Claude Desktop now?", default=True):
                try:
                    path, backup = register_claude_desktop(claude_path)
                    ui.say(f"Claude Desktop configured ({path})" + (f"; previous file saved as {backup.name}"
                                                                     if backup else ""))
                    ui.say("Quit Claude Desktop completely (also from the system tray) and open it again.")
                except BusyMCPError as exc:
                    ui.say(f"Could not update Claude Desktop: {exc}")
        else:
            ui.say("Claude Desktop is not installed for this user; skipping.")

    ui.say("\nFor other AI apps, use:")
    for app, text in snippets().items():
        ui.say(f"--- {app} ---\n{text}")
    ui.say("\nDone. Try asking your AI: \"Show my BUSY dashboard for this year\".")
    return 0
