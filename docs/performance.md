# Performance

## How reads are kept fast

1. **Masters are cached.** All of `Master1` (names, groups, types) is loaded once, so name lookups and group
   walks never touch the database.
2. **Movement index.** One aggregate query builds per-day debit/credit totals for every account, and
   per-day in/out quantity and value for every item. Prefix sums turn "movement of X between any two
   dates" into two binary searches. Trial balance, balance sheet, P&L, group summary, aging, dashboards and
   stock summary for **any period** are then computed in memory.
3. **Change detection instead of timers.** At most every `BUSY_FRESHNESS_SECONDS` (default 10 s), a cheap
   fingerprint query checks voucher and master counts, the highest codes, modification times and opening
   balances. Caches are rebuilt only when something changed. Answers therefore reflect a voucher saved in
   BUSY a few seconds earlier, without re-reading everything on every question.
4. **Prefetch.** When the server starts, a background thread loads masters and builds the index for the
   default company, so the first question doesn't wait for it.
5. **Targeted queries stay targeted.** A single account's statement or balance uses an indexed query when
   the index hasn't been built yet; it never triggers a full build.
6. **Bounded responses.** Every list is paginated so the AI receives compact JSON. Empty and zero fields are
   dropped from generic reads.

## Measurements

These numbers come from `python scripts/benchmark.py`: a synthetic SQLite company with BUSY's layout,
50,000 vouchers, 200,000 lines, 2,000 parties and 1,000 items.

| Step | Time |
|---|---|
| Version 1.0: one full aggregate query (each report ran two) | 161 ms |
| Build the movement index (once per data change) | 975 ms |
| Trial balance (index warm) | 12 ms |
| Balance sheet | 7 ms |
| Profit & loss | 4 ms |
| Receivables, 2,000 parties | 7 ms |
| Trial balance for a different period | 14 ms |
| Stock summary, 1,000 items | 8 ms |
| Dashboard | 40 ms |

* **Per report:** each report was about 320 ms in 1.0 and is 4–40 ms once the index exists, roughly
  10–50 times faster.
* **Build cost:** the one-time build costs about three 1.0 reports. Prefetch pays it in the background.
* **Where the build time goes:** about 80% is the database's own GROUP BY. Python-side work is under
  100 ms.

On the Access edition over a network share, every database round trip is slower. The benefit of answering
repeated questions from memory is larger there, and so is the one-off build. The next section measures
exactly that.

## Large companies and network folders

Many offices keep BUSY's data on one computer and open it from the others as `\\SERVER\BusyWin\DATA` or
through a mapped drive. busy-mcp then reads the company file over Windows file sharing, through Microsoft's
Access driver, just as BUSY does.

### What is slow and what isn't

* **Building the fast-read index reads every voucher once.** On a network folder, all of it crosses the
  network. It happens once when the server starts (in the background, see *Prefetch* above) and again on the
  first question after someone changes data in BUSY.
* **Every other question is answered from memory**, apart from:
  * the change check, a few small queries before a question, at most every `BUSY_FRESHNESS_SECONDS`;
  * reports that list individual entries, such as account statements, the day book, bill-wise outstanding
    and voucher details. These read only the rows they show.
* **Busy offices change data all day.** With the defaults, the first question after any saved voucher
  rebuilds the index. If that rebuild is slow on your network, check less often: `BUSY_FRESHNESS_SECONDS=120`
  rebuilds at most every two minutes, and answers may then be up to two minutes behind BUSY.
  `BUSY_CACHE_TTL` (default 900 s) also forces a rebuild after 15 minutes, even without changes. It is a
  safety net for edits the change check can't see: no count, code or modification time changed. Raising it,
  for example to 3600, means such an edit may take up to an hour to show.

### Setting up with a network folder

* Give setup the network path: `busy-mcp setup --data-dir \\SERVER\BusyWin\DATA`. A mapped drive letter
  belongs to one Windows sign-in, so a program started another way (for example "Run as administrator") may
  not see it. If the folder can't be opened, setup and `busy-mcp doctor` show Windows' own reason, for
  example *The network path was not found* or *Access is denied*.
* The Windows user that runs your AI app needs the access to the folder that BUSY users already have. While
  Microsoft's Access driver has a company file open, it keeps a small lock file (`.ldb`) next to it, even
  though busy-mcp only reads.
* `busy-mcp doctor` builds the index on your real data and reports the time ("Fast-read index built in …").
  That is the wait for the first question after a change.

### Measured on every change (CI)

The `scale-network` job in `.github/workflows/ci.yml` runs on GitHub's Windows runners. It uses 32-bit
Python 3.14 and the Access driver that ships with Windows, and does the following:

1. `scripts/benchmark_access.py` builds a company in BUSY's layout, a password-protected
   `DATA\COMP0001\db12024.bds` with:
   * 50,000 sales vouchers and 200,000 voucher lines;
   * 50,000 bill-by-bill references;
   * 2,000 parties and 1,000 items.
2. It shares the folder over SMB.
3. It runs busy-mcp's own code (`BusyDB` and the report functions) three times: on the local folder, on
   `\\localhost\busydata\DATA` and on `\\127.0.0.1\busydata\DATA`.
4. In each run it checks that:
   * the trial balance tallies;
   * the index equals direct SQL for 20 accounts × 4 periods;
   * every voucher is counted;
   * the busiest party's statement lists all its entries;
   * a repeated question runs no SQL;
   * the change check keeps the index.
5. It runs `busy-mcp setup --data-dir \\localhost\busydata\DATA` and `busy-mcp doctor` from another folder,
   and the doctor must say *ready*.

The share is a loopback to the same machine. This proves that busy-mcp works through Windows file sharing
end to end, and shows its cost. It does not show the latency or bandwidth of a real office network. Windows
can also cache the file there, because nobody else has it open. On a real network, with BUSY users working
in the same company, expect slower times, above all for the index build.

<!-- ORCHESTRATOR: fill this table from the scale-network job (step summary or the scale-network-results
     artifact, busy-benchmark.json). Do not invent numbers; leave TBD until a run has produced them. -->

| Step (seconds) | Local folder | `\\localhost\busydata\DATA` | `\\127.0.0.1\busydata\DATA` |
|---|---:|---:|---:|
| Open the company (`connect`) | TBD | TBD | TBD |
| Change check (`change_check`) | TBD | TBD | TBD |
| Load masters (`masters`) | TBD | TBD | TBD |
| Build the index: accounts + items (`index`) | TBD | TBD | TBD |
| Trial balance | TBD | TBD | TBD |
| Profit & loss | TBD | TBD | TBD |
| Balance sheet | TBD | TBD | TBD |
| Stock summary | TBD | TBD | TBD |
| Statement of the busiest party, full year | TBD | TBD | TBD |
| Bill-wise receivables, all parties | TBD | TBD | TBD |
| Dashboard | TBD | TBD | TBD |
| Day book, one month | TBD | TBD | TBD |
| Repeated trial balance (from memory) | TBD | TBD | TBD |

Run: TBD (date, commit, runner image). Company file: TBD MB.

The job fails if a check fails, if the index build takes more than 180 s, if any report takes more than
10 s, or if the change check takes more than 10 s. These limits catch only gross slowdowns.

### Measure your own PC and network

The script is in the busy-mcp folder (for example `C:\Busy_MCP`). Use the Python that `install.cmd` set up,
because its bitness matches your Access driver. The script builds its own company and never reads or changes
BUSY's data. It refuses to build into a folder that it didn't create.

```text
cd C:\Busy_MCP
rem On one PC: build 50,000 vouchers in a temporary folder, measure, clean up
.venv\Scripts\python.exe scripts\benchmark_access.py

rem Over your network: build once and keep the folder ...
.venv\Scripts\python.exe scripts\benchmark_access.py --root C:\BusyBench --build-only
rem ... copy C:\BusyBench to the computer that holds BUSY's data and share it there as BusyBench, then
rem compare the local copy with the network copy from the PC that runs your AI app:
.venv\Scripts\python.exe scripts\benchmark_access.py --data-dir C:\BusyBench\DATA --data-dir \\SERVER\BusyBench\DATA
```

* `--vouchers 200000` simulates a bigger company. Access files are limited to 2 GB.
* The output is a table (seconds per step, plus the SQL queries and rows behind each step). The results also
  go to `busy-benchmark.json`.
* `--assert-max-seconds index=60` fails the run when a step is slower than you can accept. `reports=` sets a
  limit for every report.
* Steps with many SQL queries suffer most from network latency. `index` is the wait for the first question
  after a change.
* The first location measured also pays for loading the Access driver in its `connect` step.

## Tuning

| Setting | Effect |
|---|---|
| `BUSY_FRESHNESS_SECONDS` | How often to check for changes (0 = before every tool call) |
| `BUSY_CACHE_TTL` | Upper bound on cache age even without detected changes (default 900 s) |
| `BUSY_PREFETCH=0` | Disable startup warm-up (e.g. on very slow shares) |
| `BUSY_SQL_NOLOCK=1` | SQL edition: never wait for BUSY users' locks (may read in-flight vouchers) |
