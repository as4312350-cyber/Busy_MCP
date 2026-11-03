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
| `No Microsoft Access ODBC driver for this 64-bit Python` | BUSY installs the 32-bit Access engine. Run the one-line install command again (or double-click `install.cmd`): it downloads the official 32-bit Python by itself (or install Python 3.14 32-bit yourself): it picks the Python that matches the driver and rebuilds its environment by itself. **Or** install the x64 *Access Database Engine 2016 Redistributable* (if 32-bit Office is present, run its installer from a command prompt with `/quiet`) |
| `IM002` / `IM014` errors | Driver missing, or a 32/64-bit mismatch. Same fix as above |
| `Unrecognized database format` | `busy-mcp doctor` shows the Access drivers it found, the one it uses, and the company file's format (`.mdb` or `.accdb`). If it uses Windows' old Jet driver (`Microsoft Access Driver (*.mdb)`), this BUSY file needs the Microsoft Access Database Engine (ACE): install the *Microsoft Access Database Engine 2016 Redistributable* with the same bitness as the Python that `install.cmd` chose (usually x86), then run `busy-mcp doctor` again. If it already uses ACE (`Microsoft Access Driver (*.mdb, *.accdb)`), the file may be damaged or newer than the engine: install the current 2016 Redistributable and check that BUSY itself opens the company |
| Installer: `Installing packages failed` with Python 3.15 | Python 3.15 is too new for now: pyodbc, the database driver, has no ready-made Windows package for it yet. Run the one-line install command again (or double-click `install.cmd`): it downloads the official 32-bit Python 3.14 by itself. Or install Python 3.14 yourself ([README](../README.md#installing-python-yourself-optional)). Python 3.15 can stay installed; the installer prefers 3.14 |
| Installer: `Installing packages failed` with Python 3.10–3.14 | Usually no internet access, or a proxy. The installer only downloads ready-made packages, so no compiler is needed |
| Installer: `The download failed` | The installer could not download its 32-bit Python from `api.nuget.org`: no internet access, or a proxy or firewall blocks it. Install Python yourself ([README](../README.md#installing-python-yourself-optional)) and run `install.cmd` again |
| Installer: `The downloaded file is not the expected one` | The download was changed on its way, often by a proxy or a Wi-Fi sign-in page, so the installer refused to use it. Try again on another network, or install Python yourself ([README](../README.md#installing-python-yourself-optional)) |
| Installer: `The old environment is in use` or `The folder .python32 is in use` | Quit Claude Desktop (also from the system tray) and any other AI app using BUSY, then run `install.cmd` again |
| Installer picked the wrong Python | The first lines show the Access driver it found and the Python it chose. To force one: `powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Python "C:\path\to\python.exe"`. To use the installer's own 32-bit Python (`.python32`) instead: type `cmd` in the project folder's address bar, press Enter, and run `install.cmd -BootstrapPython` |
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
