# BUSY feature coverage

This page maps BUSY's features to the tools that read them and the tables behind them.

**Confidence** (details and sources in [busy-internals.md](busy-internals.md)):
* **confirmed:** two independent sources, or a live-database study plus the schema dump
* **likely:** a single strong source

Anything without a dedicated tool is still readable with `catalog` + `read-table`.

| BUSY feature | Tool(s) | Tables | Confidence |
|---|---|---|---|
| Account groups and accounts | `chart-of-accounts`, `list-masters`, `master-details`, `ledger-details` | Master1 (types 1, 2), Folio1, MasterAddressInfo | confirmed |
| Vouchers (all types) | `day-book`, `voucher-details`, `voucher-types` | Tran1, Tran2 | confirmed |
| Cancelled vouchers | excluded everywhere; flagged in `voucher-details` | Tran1.Cancelled / VchCancelled | confirmed |
| Narration | `day-book`, `voucher-details`, `ledger-statement` | VchOtherInfo, Tran1, Tran2.ShortNar | confirmed |
| Ledger, TB, P&L, balance sheet | `ledger-statement`, `trial-balance`, `profit-and-loss`, `balance-sheet`, `group-summary`, `ledger-monthly` | Tran2 RecType 1, Folio1 | confirmed |
| Bill-by-bill outstanding, due dates | `bills-outstanding` | Tran3 (Method 1 new, 2 against) | likely |
| Credit days | `bills-outstanding`, `master-details` | Master1.I2 | likely |
| Interest calculation | not computed (BUSY report); bills and due dates are available | Tran3 | — |
| Bank reconciliation | `bank-reconciliation` | Tran2.ClrDate, BRSOpBal | likely |
| Post-dated cheques | `post-dated-cheques` | Tran1.StockUpdationDate | likely |
| Cost centres, budgets | `read-table` (tables vary by version) | Tran4/Tran5, masters | unverified |
| GST | `gst-summary`, `voucher-details` (GST lines) | tax ledgers, Tran1.CM1 (Sale/Purchase Type), VchGSTSumItemWise | likely |
| GST return data (GSTR-1/2A/2B/3B) | `read-table` | GSTR*Info | likely |
| TDS / TCS | `tds-register`, `voucher-details` | TDS | confirmed |
| Items, units, tax category, HSN, prices | `master-details`, `list-masters` | Master1 (types 6, 8, 25) | confirmed (unit, sale price), likely (others) |
| Stock summary and item ledger | `stock-summary`, `stock-item-movement` | Tran2 item lines (RecType 2 + detected 7/20), Folio1 | confirmed |
| Material centres (godowns) | `stock-by-location`, `voucher-details` | Master1 type 11, Tran2.MasterCode2, Tran4 | likely |
| Batches, expiry, MRP-wise | `batch-stock` | ItemParamDet (C1), Tran3 RecType 2 | likely |
| Serial numbers, warranty | `serial-numbers` | ItemSerialNo | confirmed |
| Sales / purchase orders | `pending-orders`, `voucher-details` | Tran3 RecType 4 | likely |
| Challans | `day-book` (types 4, 11), `voucher-details` | Tran1/Tran2 | likely |
| Stock transfer, stock journal, production | `stock-summary`, `stock-item-movement`, `voucher-details` | Tran2 | likely |
| Sale / Purchase types | `gst-summary`, `voucher-details` | Master1 types 13/14, Tran1.CM1 | confirmed |
| Voucher series | `day-book`, `voucher-details` | Tran1.VchSeriesCode | likely |
| Billing / transport details | `voucher-details`, `find` | BillingDet, VchOtherInfo | confirmed |
| POS payment modes | `voucher-details` | POSDet | confirmed |
| Job work, AMC/warranty, installations | `read-table` | JobFinishedRefs, AMCWarrDet, ItemSNInstallDet | likely |
| Payroll (loans / advances) | `read-table` | EmpLADet | likely |
| Audit trail, deleted vouchers | `audit-trail` | Tran1 audit columns, DeletedInfo, CheckList, EventLog | confirmed |
| Company settings | blocked by default (may hold credentials) | Config | — |
