# Troubleshooting

Start with `busy-mcp --check`. It runs discovery, opens the selected database, prints the detected
capabilities, and checks that the trial balance tallies.

| Symptom | Cause / fix |
|---|---|
| `No Microsoft Access ODBC driver is visible to this 64-bit Python` | BUSY installs the 32-bit Access engine. Either install 32-bit Python and re-run `scripts\install.ps1`, or install the x64 *Access Database Engine 2016 Redistributable*. If 32-bit Office is present, run that installer from a command prompt with `/quiet` |
| `IM002` / `IM014` errors | Driver missing, or a 32/64-bit mismatch. Same fix as above |
| `Not a valid password` | Set `BUSY_ACCESS_PASSWORD` |
| `BUSY data folder not found` | Set `BUSY_DATA_DIR` |
| No companies found (Access) | The folder must contain `COMPnnnn\db1YYYY.bds` files. Point `BUSY_DATA_DIR` at the folder that *contains* `COMP0001` |
| No companies found (SQL) | The login must be able to see the `BusyCOMPnnnn_db1YYYY` databases. Run `scripts/create_readonly_sql_login.sql` again after a new FY is created |
| Cannot reach SQL Server | Check the instance name or port, that TCP/IP is enabled in SQL Server Configuration Manager, the firewall, and that SQL Browser is running for named instances |
| Trial balance `warning` / non-zero difference | Opening balances weren't carried forward into this FY database (run BUSY's carry-forward), or opening stock differs |
| Balance sheet `difference` | Usually equals the closing-stock value, which is ledger-based and excluded |
| Voucher shows `(inferred)` or `(unmapped)` | Set `BUSY_VCH_TYPE_NAMES`, and please open a *Schema report* issue |
| Stock in/out looks reversed | Check `server-info` → `item_inward_sign_rule`, and set `BUSY_ITEM_INWARD_SIGN` |
| New masters not visible | Call `refresh-cache`, or wait for `BUSY_CACHE_TTL` |
| Responses cut off | Use `offset`/`limit` (follow `next_offset`) |

For deeper diagnosis, set `BUSY_LOG_LEVEL=INFO` and `BUSY_LOG_FILE=C:\Busy_MCP\busy-mcp.log`.
