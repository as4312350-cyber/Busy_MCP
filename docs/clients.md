# Connecting AI clients

Replace `C:\Busy_MCP` with the folder you cloned into. In JSON, every backslash is written twice.

## Claude Desktop

Go to Settings → Developer → Edit Config. See [`examples/claude_desktop_config.json`](../examples/claude_desktop_config.json)
for Access and SQL edition entries. Restart Claude after saving.

## Claude Code

```powershell
claude mcp add busy -e BUSY_DATA_DIR=C:\BusyWin\DATA -- C:\Busy_MCP\.venv\Scripts\busy-mcp.exe
```

## Cursor

Add the server to `%USERPROFILE%\.cursor\mcp.json`. See [`examples/cursor_mcp.json`](../examples/cursor_mcp.json).

## VS Code (GitHub Copilot agent mode)

Add the server to `.vscode/mcp.json`. See [`examples/vscode_mcp.json`](../examples/vscode_mcp.json).

## ChatGPT and other remote clients

Web apps can't start a program on your PC, so they need an HTTPS URL:

```powershell
$env:BUSY_MCP_TOKEN = "<at least 32 random characters>"
C:\Busy_MCP\.venv\Scripts\busy-mcp.exe --http --host 127.0.0.1 --port 8765
```

Expose `http://127.0.0.1:8765/mcp` through an HTTPS tunnel or reverse proxy (Cloudflare Tunnel,
Caddy, ngrok), and configure the client to send `Authorization: Bearer <token>`.

**Think before you do this:**
* Your books leave your network and go to the AI provider.
* The endpoint is reachable from the internet, protected by one token.

Keep the token secret, rotate it, and stop the server when you aren't using it.
