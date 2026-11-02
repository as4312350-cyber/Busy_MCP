# BUSY internals: research notes

These notes explain why the server is built the way it is. Confidence tags:
**[Confirmed]** = seen in a real BUSY database by a cited source,
**[Likely]** = strong inference, **[Unverified]** = a guess, kept out of report logic.

## 1. Integration options BUSY offers

| Route | Status | Why it isn't used / is used |
|---|---|---|
| Official REST/XML API | Doesn't exist publicly [Confirmed] | BUSY's FAQ says there's no built-in third-party integration, and API work goes through channel partners ([busy.in FAQ](https://busy.in/faqs/third-party-services/integration/1/)) |
| COM DLL `Busy2L21.CFixedInterface` | Exists [Confirmed] | It has `GetRecordset(sql)`, `SaveVchFromXML(vchType, xml, err, …)` and `ValidUserPassword`. But it's 32-bit, can only have one company open per process, takes about 7 s to switch companies, isn't documented, and needs BUSY installed on the same PC. Seen in a real VB6 integration (ManojMathuria/Easy-Publish-Prime) and the `devstroop/busy-bridge` project |
| Read the database directly | Works [Confirmed] | Same data BUSY itself reads, works on both editions, can be opened read-only, no BUSY licence add-on needed. **Chosen.** |
| Excel / XML export files | Works but manual | Not live, so it doesn't fit an AI assistant |

## 2. Where the data lives

* **Access edition** [Confirmed]: `<data dir>\COMP0001\db12024.bds` (MS-Access/Jet format,
  password-protected), one file per FY. `db.bds` in the same folder holds company-level data.
  Default data dir is `C:\BusyWin\DATA` (BUSY > Company > *Set Data Directory*). A public
  ledger-viewer project opens these files with `Microsoft.ACE.OLEDB.12.0` and BUSY's standard
  `.bds` password ([gitrkvhub/busy-ledger-viewer](https://github.com/gitrkvhub/busy-ledger-viewer)).
* **SQL edition** [Confirmed]: databases `BusyCOMP0001_db12024`, `BusyCOMP0001_db12025`, … on
  the BUSY SQL Server instance. The suffix is the **FY start year** (db12026 holds vouchers from
  2026-04-01). `BusyCOMP0001_db` without a year is a system/company database with no `Tran1`.
  It's reachable with the plain "SQL Server" ODBC driver, with no BUSY-specific driver needed
  ([prateeknagwani/busy-extraction-poc](https://github.com/prateeknagwani/busy-extraction-poc) FINDINGS.md).

## 3. Tables

| Table | Meaning | Key columns |
|---|---|---|
| `Master1` | All masters [Confirmed] | `Code` (PK), `MasterType`, `Name`, `Alias`, `PrintName`, `ParentGrp` |
| `Folio1` | Opening balances [Confirmed] | `MasterCode`, `MasterType`, `D1` (ledger opening, **neg = Dr**; item opening qty), `D3` (item opening value) |
| `MasterAddressInfo` | Party address, GSTIN, contacts [Confirmed] | `MasterCode`, `GSTNo`, address fields |
| `Tran1` | Voucher header [Confirmed] | `VchCode`, `VchType`, `VchNo`, `Date`, `MasterCode1` (party), `VchAmtBaseCur` (total incl. tax), `VchSalePurcAmt` (goods excl. tax, **not** the invoice total) |
| `Tran2` | Voucher lines [Confirmed] | `VchCode`, `RecType` (1 = ledger, 2 = item), `MasterCode1`, `Value1` (signed amount / qty), `Value3` (item value excl. tax), `D2/D4/D9` (net rate / list price / discount %), `SrNo`, `Date`, `VchNo` |
| `Tran3` | Order lines (RecType 4) [Confirmed]; bill references (other RecTypes) [Unverified] | `VchCode`, `RecType`, `MasterCode1`, `Value1` qty, `Value3` rate, `NewRefAmount`, `RefCode`, `DueDate` |
| `VchGSTSumItemWise` | GST per item line [Confirmed] | `ItemCode`, `VchDate`, `TaxRate`, `TaxRate1` |
| `DailySum`, `VchOtherInfo`, `MastFootPrint`, `Tran7/10/12` | Seen, purpose partly known | – |

`Master1.MasterType`: 1 Account Group, 2 Account, 5 Item Group, 6 Item [Confirmed]. About 35
other values exist (units, material centres, series, tax categories …) [Unverified labels].
`ParentGrp = 0` means top level.

## 4. Sign convention [Confirmed]

* Ledger amounts: **negative = Debit, positive = Credit**, on every line of `Tran2`
  (`RecType=1`) and in `Folio1.D1`. Every voucher's ledger lines sum to zero.
* Item opening stock in `Folio1.D1/D3`: positive = stock in hand (company-wide `SUM(D3)` equals
  the Stock-in-hand ledger's opening, to the paisa).
* Item movement direction in `Tran2.Value1`: **detected at runtime** from purchase vouchers
  (type 2), because no public source pins it down. Can be overridden with `BUSY_ITEM_INWARD_SIGN`.

## 5. Voucher type codes (`Tran1.VchType`)

| Code | Label | Evidence |
|---|---|---|
| 2 | Purchase | [Confirmed] real supplier invoices |
| 3 | Sales Return (Credit Note with items) | [Confirmed] "CN/.." against customers |
| 9 | Sales | [Confirmed] party Dr, Sales + GST Cr |
| 10 | Purchase Return (Debit Note with items) | [Confirmed] "DN/.." posting party + Purchase |
| 12 | Sales Order | [Confirmed] "SO/..", no Tran2 rows, lines in Tran3 |
| 13 | Purchase Order | [Confirmed] a real integration calls `SaveVchFromXML(13, "<PurchaseOrder>…")` |
| 15 | Contra | [Confirmed] two bank ledgers, nothing else |
| 16 | Journal | [Confirmed] "JE/.." |
| 14 | Receipt | [Likely] the POC labelled it "Payment", but its own data shows the **bank debited** (negative) and the party credited, which is a receipt under BUSY's sign rule |
| 19 | Payment | [Likely] "PC/.." petty-cash payments |
| 17 / 18 | Credit Note / Debit Note without items | [Likely] post to nominal "CREDIT NOTE" / "DEBIT NOTE" ledgers |
| 5 / 8 | Stock Transfer / Stock Journal | [Likely] item lines only, same item ± same qty |

The report maths never depends on these labels. Only `sales-purchase-analysis` defaults to
codes 9/3 and 2/10 (all confirmed), and you can override those per call.

## 6. Design consequences

* **Python + pyodbc:** one driver stack covers Access and SQL Server, supports Windows
  authentication with no C++ build tools, and supports ODBC read-only mode. The Node `mssql`
  driver can't do Windows auth without a native build, which is exactly what blocked the POC.
* **Portable SQL only:** single `INNER JOIN`s, `IIF`, bind parameters, `[Date]` quoting. The same
  SQL runs on Jet, T-SQL and SQLite, so the test fixture exercises the real queries. Tree walks
  and name lookups happen in Python on a cached `Master1`.
* **Every derived figure states its method.** Aging is a FIFO estimate. P&L and balance sheet
  are ledger-based and exclude stock valuation. Each of these says so in its output, so the AI
  can't present an estimate as BUSY's own number.

## 7. Open questions (contributions welcome)

1. `Tran3` bill-reference layout. This would allow true bill-wise outstanding with due dates.
2. Which `Tran1` column marks cancelled / optional vouchers.
3. Narration column name(s).
4. Units / material-centre `MasterType` codes and their link from item lines.
5. Company name and address table in the Access edition's `db.bds`.

If you have a BUSY install, run `master-type-summary`, `voucher-types` and
`describe-table Tran1` and open an issue with the (anonymised) output.
