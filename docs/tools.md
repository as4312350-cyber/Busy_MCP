# Tool reference

All tools are read-only and annotated `readOnlyHint: true`, `destructiveHint: false`. Every tool
except `schema-guide` and `list-companies` also accepts `company` (e.g. `COMP0002`) and `fy` (FY start
year, e.g. `2023`), so you can query another company or year without switching.

Lists are paginated with `offset` and `limit`. Responses include `total`, `returned`, `has_more` and
`next_offset`.

## Discovery

| Tool | Purpose | Key arguments |
|---|---|---|
| `server-info` | Version, selected database, detected capabilities, settings (secrets masked) | – |
| `list-companies` | Every company/FY database found | – |
| `select-company` | Change the default company/year | `company`, `fy` |
| `company-overview` | Company name, master counts, voucher date range | – |
| `voucher-types` | Voucher types used, with counts and sample numbers | – |
| `master-type-summary` | Master counts by `MasterType` with examples | – |
| `schema-guide` | How BUSY stores data, plus SQL dialect tips | – |
| `list-tables` / `describe-table` | Schema exploration with sample rows | `table` |
| `query-database` | One read-only `SELECT` (capped) | `sql`, `max_rows` |
| `refresh-cache` | Reload masters after edits in BUSY | – |

## Masters

| Tool | Purpose | Key arguments |
|---|---|---|
| `search-masters` | Find accounts/groups/items by part of a name | `text`, `kind` |
| `list-masters` | List one kind, optionally under a group | `kind`, `under_group` |
| `chart-of-accounts` | Group tree | `include_ledgers`, `item_groups` |
| `ledger-details` | Group path, opening balance, address, GSTIN, phone, email | `ledger` |

## Accounts

| Tool | Purpose | Key arguments |
|---|---|---|
| `ledger-balance` | Balance on a date | `ledger`, `as_of` |
| `ledger-statement` | Entries with contra account, narration and running balance | `ledger`, `from_date`, `to_date` |
| `trial-balance` | Per account and per primary group, with a tally check | `from_date`, `to_date`, `group`, `ledger_level` |
| `group-summary` | Children of a group with balances | `group` |
| `profit-and-loss` | Income and expense heads (before stock adjustment) | `from_date`, `to_date` |
| `balance-sheet` | Assets and liabilities by primary group | `as_of` |
| `outstanding` | Receivable/payable with 0-30 … 180+ aging | `nature`, `as_of`, `min_amount` |

## Vouchers, trade, inventory

| Tool | Purpose | Key arguments |
|---|---|---|
| `day-book` | Vouchers with party, amount, series, narration | `from_date`, `to_date`, `vch_type`, `party` |
| `voucher-details` | Header, entries, items (qty, rate, discount), order lines | `vch_code` or `vch_no` |
| `sales-purchase-analysis` | Totals by party, item, month or voucher, net of returns | `kind`, `group_by` |
| `stock-summary` | Opening, in, out and closing qty per item | `item_group`, `search`, `only_negative` |
| `stock-item-movement` | Movements of one item with running qty | `item` |

## Prompts

| Prompt | What it does |
|---|---|
| `financial-health-check` | Checks the TB tallies, then reviews P&L, balance sheet, receivables >90 days and negative stock |
| `receivables-follow-up` | Builds a chase list with contacts and last receipt date |
| `month-end-review` | Compares sales, purchases, unusual vouchers and cash with the previous month |
