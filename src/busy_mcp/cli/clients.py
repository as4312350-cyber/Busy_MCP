"""Registering busy-mcp with AI desktop clients."""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from ..config import config_dir, config_path
from ..errors import BusyConnectionError

SERVER_KEY = "busy"


def claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def server_entry() -> dict[str, Any]:
    """How an MCP client should launch this installation."""
    entry: dict[str, Any] = {"command": sys.executable, "args": ["-m", "busy_mcp"]}
    if config_path() != config_dir() / "config.json":
        entry["env"] = {"BUSY_MCP_CONFIG": str(config_path())}
    return entry


def register_claude_desktop(path: Path | None = None) -> tuple[Path, Path | None]:
    """Add/replace the 'busy' server in Claude Desktop's config.

    The existing file is backed up first and written atomically. Returns
    (config path, backup path or None if the file did not exist)."""
    path = path or claude_desktop_config_path()
    data: dict[str, Any] = {}
    backup = None
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as exc:
            raise BusyConnectionError(
                f"{path} is not valid JSON, so it was left untouched. Fix or delete it and run setup again."
            ) from exc
        if not isinstance(data, dict):
            raise BusyConnectionError(f"{path} does not contain a JSON object; left untouched.")
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise BusyConnectionError(f"'mcpServers' in {path} is not an object; left untouched.")
    servers[SERVER_KEY] = server_entry()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, backup


def snippets() -> dict[str, str]:
    """Config snippets for other clients."""
    entry = server_entry()
    one = json.dumps({"mcpServers": {SERVER_KEY: entry}}, indent=2)
    vscode = json.dumps({"servers": {SERVER_KEY: {"type": "stdio", **entry}}}, indent=2)
    args = " ".join(entry["args"])
    return {
        "Cursor (~/.cursor/mcp.json)": one,
        "VS Code (.vscode/mcp.json)": vscode,
        "Claude Code (terminal)": f'claude mcp add {SERVER_KEY} -- "{entry["command"]}" {args}',
    }
