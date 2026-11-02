# Troubleshooting

Start with **`busy-mcp doctor`**. It checks the following in order and prints `[OK]`, `[WARN]` or `[FAIL]`
with the fix:

1. Python and the MCP SDK
2. ODBC drivers
3. Settings, and where each came from
4. Company discovery
5. Opening the latest company and building the fast-read index
6. Whether the trial balance tallies

If you used `install.cmd`, run it as `C:\Busy_MCP\.venv\Scripts\python.exe -m busy_mcp doctor`.

| Symptom | Cause / fix |
|---|---|
| `No Microsoft Access ODBC driver for this 64-bit Python` | BUSY installs the 32-bit Access engine. Install 32-bit Python and run `install.cmd` again, **or** install the x64 *Access Database Engine 2016 Redistributable* (if 32-bit Office is present, run its installer from a command prompt with `/quiet`) |
| `IM002` / `IM014` errors | Driver missing, or a 32/64-bit mismatch. Same fix as above |
| `Not a valid password` | Set `BUSY_ACCESS_PASSWORD` |
| No companies found (Access) | Point `BUSY_DATA_DIR` at the folder that *contains* `COMP0001` (BUSY > Company > Set Data Directory). `busy-mcp setup` searches all drives |
| No companies found (SQL) | The login must see the `BusyCOMPnnnn_db1YYYY` databases. Run `scripts/create_readonly_sql_login.sql` again after BUSY creates a new year |
| Cannot reach SQL Server | Check the instance name or port, that TCP/IP is enabled (SQL Server Configuration Manager), the firewall, and that SQL Browser is running for named instances |
| Trial balance `warning` | Opening balances weren't carried forward into this FY database (run BUSY's carry-forward), or opening stock differs |
| Balance sheet `difference` | Usually equals the closing-stock value (ledger-based statements exclude stock valuation) |
| `(inferred)` or `(unmapped)` voucher types | Set `BUSY_VCH_TYPE_NAMES`, and please open a *Schema report* issue |
| Stock in/out reversed or doubled | Check `server-info` → `item_inward_sign_rule` / `item_line_rectypes_rule`; override with `BUSY_ITEM_INWARD_SIGN` / `BUSY_ITEM_RECTYPES` |
| `bills-outstanding` says no bill-wise data | Bill-by-bill isn't maintained for those parties in BUSY; use `outstanding` (FIFO aging) |
| A table is "blocked" | It may contain stored credentials. Administrators can set `BUSY_ALLOW_SENSITIVE=1` |
| Claude Desktop doesn't show BUSY | Quit Claude completely (system tray) and reopen. Check `%APPDATA%\Claude\claude_desktop_config.json` contains `"busy"` |
| Slow first answer | Normal: the fast-read index is being built. Later answers take milliseconds ([performance.md](performance.md)) |

For deeper diagnosis, set `BUSY_LOG_LEVEL=INFO` and `BUSY_LOG_FILE=C:\Busy_MCP\busy-mcp.log`.
