# Production checklist

Every change to busy-mcp is tested automatically on Windows and Linux against a company that uses BUSY's
exact table layout:
- stored as a real password-protected Access file and read through Microsoft's own Access driver with
  32-bit Python, as on a BUSY PC
- installed with the same `install.cmd` you use

What automation cannot check is **your** data: your BUSY version, your company's conventions, your volume
of vouchers. This checklist compares busy-mcp's answers with your BUSY in about 15 minutes. Run it once after
installing and again after every BUSY upgrade. If every number matches, you can rely on it in production.

## 1. Install and diagnose (5 minutes)

1. Double-click `install.cmd` and follow the questions (see the [README](../README.md#install-windows-about-5-minutes)).
2. Run the diagnosis:

   ```text
   C:\Busy_MCP\.venv\Scripts\python.exe -m busy_mcp doctor
   ```

   Every line must be `[ OK ]` or `[INFO]`, and the last line must say **ready**. A `[FAIL]` line names the
   fix; [troubleshooting.md](troubleshooting.md) has the details.
3. Restart Claude Desktop (quit it from the system tray too) and ask: *"Show my BUSY dashboard for this year."*

## 2. Acceptance test: verify, then compare five numbers with BUSY (10 minutes)

1. Run `busy-mcp verify`:

   ```text
   C:\Busy_MCP\.venv\Scripts\python.exe -m busy_mcp verify
   ```

   It reads your company (it changes nothing) and checks, in plain words, that:
   - every voucher balances
   - the trial balance tallies
   - busy-mcp's fast summary agrees with your vouchers
   - pending bills add up to each party's ledger balance
   - the stock summary adds up
   - opening balances were carried forward into this year

   No line may say `[FAIL]`. A `[WARN]` line explains something to keep in mind, and `[INFO]` lines are
   counts. Then it works out the five numbers in the table below and says where to find each one in BUSY.

   - `--party "Name"` and `--item "Name"` use a customer or item you know well for numbers 2, 4 and 5.
   - `--company COMP0002` and `--fy 2023` check another company or financial year.
   - `--redact --out verify.txt` saves a copy with names replaced by "Party 1", "Item 1" and so on, safe
     to share. `--json` gives the same report in machine-readable form.

2. Compare the five numbers with BUSY's own screens. Use the same company, financial year and dates in both
   places. You can also ask the AI: it uses the same reports, so its answers are the same numbers.

| # | `busy-mcp verify` shows (or ask the AI) | Compare with BUSY | Must match |
|---|---|---|---|
| 1 | Trial balance totals ("Trial balance as on 31 March, group totals only") | BUSY's Trial Balance (Display menu) | Total debit = total credit, and the difference (`net_difference`) is 0.00 |
| 2 | Ledger of the customer with the largest balance ("Ledger of *<a customer you know well>* for this year") | BUSY's ledger of that account | Opening and closing balance |
| 3 | Sales per month ("Total sales by month this year") | BUSY's Sales Register / monthly summary | Each month's total |
| 4 | Closing stock of the item with the most movement ("Closing stock of *<an item you know well>*") | BUSY's Stock Status for that item | Closing quantity |
| 5 | Pending bills of the party with the most bills ("Bill-wise outstanding of *<a customer with pending bills>*") | BUSY's Outstanding / bill-wise report | Pending amount of each bill |

If `busy-mcp verify` says the trial balance differs by exactly the value of your opening stock, that is
expected: busy-mcp's trial balance lists accounts only and leaves stock out.

**All five match:** you're ready. Keep a note of the date and BUSY version.

**A number differs:** don't use that report yet. Send the output of `busy-mcp doctor`, the report saved by
`busy-mcp verify --redact --out verify.txt` and the tool's answer (open an issue with names and amounts
removed). Most differences come from a local convention that one setting fixes, for example
`BUSY_PL_GROUPS`, `BUSY_DEBTOR_GROUPS`, `BUSY_ITEM_RECTYPES` or `BUSY_VCH_TYPE_NAMES`
([configuration.md](configuration.md)).

Reports that say `method`, `confidence: likely` or `caveat` describe how they were calculated. Read those
fields before relying on the number.

## 3. Operating it

| Topic | Recommendation |
|---|---|
| **Data safety** | busy-mcp only reads. It never writes to BUSY, and every connection is opened read-only. Keep your normal BUSY backups regardless |
| **SQL edition** | Create the read-only login with `scripts/create_readonly_sql_login.sql` and use it in setup, so SQL Server itself enforces read-only access |
| **Sensitive data** | Leave `BUSY_ALLOW_SENSITIVE` off. The AI sees what a BUSY user with full view rights sees, except stored credentials |
| **Audit** | For a record of every free-form query, set `BUSY_LOG_LEVEL=INFO` and `BUSY_LOG_FILE=C:\Busy_MCP\busy-mcp.log` |
| **New financial year** | Nothing to do: new year databases are found automatically. If BUSY's year-end carry-forward wasn't run, the trial balance shows a warning |
| **Updates** | Download the new version over the old folder and double-click `install.cmd` again. Your settings in `%APPDATA%\busy-mcp\config.json` are kept. Run this checklist again |
| **Remote use (ChatGPT etc.)** | Only behind HTTPS with a long `BUSY_MCP_TOKEN`; see [clients.md](clients.md). Local use (Claude Desktop) needs no network access at all |

## 4. Rollback and uninstall

* **Disconnect from Claude Desktop:** setup saved your previous Claude configuration next to it as
  `claude_desktop_config.json.bak-<date>` in `%APPDATA%\Claude`. Restore that file, or delete the `"busy"`
  entry.
* **Remove busy-mcp completely:** also delete the project folder (e.g. `C:\Busy_MCP`) and
  `%APPDATA%\busy-mcp`.

BUSY itself is untouched in every case.
