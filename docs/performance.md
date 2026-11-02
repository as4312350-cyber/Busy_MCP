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
repeated questions from memory is larger there, and so is the one-off build.

## Tuning

| Setting | Effect |
|---|---|
| `BUSY_FRESHNESS_SECONDS` | How often to check for changes (0 = before every tool call) |
| `BUSY_CACHE_TTL` | Upper bound on cache age even without detected changes (default 900 s) |
| `BUSY_PREFETCH=0` | Disable startup warm-up (e.g. on very slow shares) |
| `BUSY_SQL_NOLOCK=1` | SQL edition: never wait for BUSY users' locks (may read in-flight vouchers) |
