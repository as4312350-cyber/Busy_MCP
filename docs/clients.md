# Connecting AI clients

## Claude Desktop (automatic)

`busy-mcp setup` adds BUSY to Claude Desktop for you. It backs up `claude_desktop_config.json` first and
keeps any other servers you have. Quit Claude Desktop completely (including from the system tray) and
open it again.

To do it by hand, go to Settings → Developer → Edit Config. Every backslash is written twice in JSON:

```json
{
  "mcpServers": {
    "busy": { "command": "C:\\Busy_MCP\\.venv\\Scripts\\python.exe", "args": ["-m", "busy_mcp"] }
  }
}
```

## Cursor, VS Code, Claude Code

`busy-mcp setup` prints ready-to-paste snippets for each. Examples are in
[`examples/`](../examples):

* **Cursor:** `%USERPROFILE%\.cursor\mcp.json` (see `examples/cursor_mcp.json`)
* **VS Code (Copilot agent mode):** `.vscode/mcp.json` (see `examples/vscode_mcp.json`)
* **Claude Code:**

  ```powershell
  claude mcp add busy -- C:\Busy_MCP\.venv\Scripts\python.exe -m busy_mcp
  ```

## ChatGPT and other remote clients

Web apps can't start a program on your PC, so they need an HTTPS URL:

```powershell
$env:BUSY_MCP_TOKEN = "<at least 32 random characters>"
C:\Busy_MCP\.venv\Scripts\python.exe -m busy_mcp serve --http --host 127.0.0.1 --port 8765
```

Put an HTTPS tunnel or reverse proxy (Cloudflare Tunnel, Caddy, ngrok) in front of
`http://127.0.0.1:8765/mcp`, and configure the client to send `Authorization: Bearer <token>`.

**Think before you do this:**
* Your books leave your network and go to the AI provider.
* The endpoint is reachable from the internet, protected by one token.

Keep the token secret, rotate it, and stop the server when you aren't using it.
