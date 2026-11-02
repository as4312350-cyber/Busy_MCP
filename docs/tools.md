# Tool reference

All 44 tools are read-only (`readOnlyHint: true`, `destructiveHint: false`). Unless noted, every tool also
accepts `company` (e.g. `COMP0002`) and `fy` (FY start year, e.g. `2023`), so another company or year can be
queried without switching. Lists are paginated with `offset`/`limit`; responses carry `total`, `returned`,
`has_more` and `next_offset`.

Reports state how reliable they are:
* **`method`:** how a figure was derived.
* **`confidence`:** whether the underlying BUSY schema fact is confirmed or likely.
* **`caveat`:** what the figure does not include.
* **`warning`:** something in the data looks wrong.

## Start here

| Tool | Purpose |
|---|---|
| `dashboard` | Sales, purchases, profit, receivables, payables, cash & bank, top debtors, negative stock, voucher counts |
| `find` | Search everything: names, phones, GSTIN/PAN, e-mails, invoice numbers, narrations, bill/batch/serial numbers |

## Discovery

| Tool | Purpose | Key arguments |
|---|---|---|
| `server-info` | Version, selected database, detected BUSY features, cache state, settings (secrets masked) | – |
| `list-companies` / `select-company` | Companies and years found; change the default | `company`, `fy` |
| `company-overview` | Company name, master counts, voucher date range | – |
| `voucher-types` | Voucher types in use, with counts and sample numbers | – |
| `master-type-summary` | Masters per type (units, godowns, sale types, series, …) with examples | – |
| `schema-guide` | How BUSY stores data, plus SQL dialect tips | – |
| `refresh-cache` | Force a reload (normally automatic within seconds of a change) | – |

## Reading anything

| Tool | Purpose | Key arguments |
|---|---|---|
| `catalog` | Every table grouped by BUSY feature, with what it holds | `with_row_counts` |
| `read-table` | Read any table without SQL: columns, filters (`eq ne gt ge lt le contains in is_null not_null`), dates, sort, pages; codes decoded to names | `table`, `columns`, `filters`, `date_from`, `date_to`, `order_by` |
| `master-details` | Everything about one master of any type, decoded | `name_or_code`, `master_type` |
| `list-tables` / `describe-table` | Schema and sample rows | `table` |
| `query-database` | One read-only `SELECT` (last resort) | `sql`, `max_rows` |

## Accounts

| Tool | Purpose | Key arguments |
|---|---|---|
| `search-masters` / `list-masters` / `chart-of-accounts` | Find and browse accounts, groups, items | `text`, `kind`, `under_group` |
| `ledger-details` | Group path, opening balance, address, GSTIN, phone, e-mail | `ledger` |
| `ledger-balance` | Balance on a date | `ledger`, `as_of` |
| `ledger-statement` | Entries with contra account, narration, running balance | `ledger`, `from_date`, `to_date` |
| `ledger-monthly` | Month-wise debit/credit/closing for an account **or a whole group** | `account_or_group` |
| `trial-balance` | Per account and primary group, with a tally check | `from_date`, `to_date`, `group`, `ledger_level` |
| `group-summary` | Children of a group with balances | `group` |
| `profit-and-loss` / `balance-sheet` | Ledger-based statements (before stock adjustment) | dates |
| `cash-bank-summary` | Every cash/bank account plus monthly cash flow | dates |

## Parties and banking

| Tool | Purpose | Key arguments |
|---|---|---|
| `bills-outstanding` | Pending bills with bill/due dates, overdue days, aging, reconciliation to ledger | `nature`, `party`, `as_of`, `overdue_only` |
| `outstanding` | Party balances with FIFO aging (works without bill-wise data) | `nature`, `as_of`, `min_amount` |
| `bank-reconciliation` | Books vs bank, uncleared deposits and cheques | `bank`, `as_of` |
| `post-dated-cheques` | Receipts and payments dated in the future | `as_of` |

## Vouchers and trade

| Tool | Purpose | Key arguments |
|---|---|---|
| `day-book` | Vouchers with party, amount, series, narration | `from_date`, `to_date`, `vch_type`, `party` |
| `voucher-details` | Complete voucher, end to end (see below) | `vch_code` or `vch_no` |
| `sales-purchase-analysis` | Totals by party, item, month or voucher, net of returns | `kind`, `group_by` |
| `pending-orders` | Ordered / executed / pending quantity and value per order line | `kind`, `party`, `item` |
| `audit-trail` | Created / modified / deleted, by whom and when | `from_date`, `to_date`, `user` |

`voucher-details` returns everything this BUSY version stores for a voucher:
* header: series, sale/purchase type, godown, created/modified by
* narration, accounting entries, and item lines (qty, rate, discount, godown)
* bill sundries, bill references, order lines
* GST lines, TDS, serial numbers, batches
* billing and transport details, POS payment split, packing

## Inventory and taxes

| Tool | Purpose | Key arguments |
|---|---|---|
| `stock-summary` | Opening, in, out, closing qty per item | `item_group`, `search`, `only_negative` |
| `stock-item-movement` | Movements of one item with running qty and godown | `item` |
| `stock-by-location` | Closing qty per item per material centre (godown) | `item`, `location` |
| `batch-stock` | Batch-wise qty with mfg/expiry dates, MRP and rates | `item`, `expiring_within_days` |
| `serial-numbers` | Serials in stock or sold, last voucher, warranty | `item`, `serial` |
| `gst-summary` | Output vs input GST by head, net payable, taxable turnover by tax type | dates, `monthly` |
| `tds-register` | TDS/TCS deducted with challan and quarter | dates, `party` |

## Prompts

`financial-health-check`, `receivables-follow-up`, `month-end-review`, `stock-review` and `audit-check` chain
the tools above into complete reviews.
