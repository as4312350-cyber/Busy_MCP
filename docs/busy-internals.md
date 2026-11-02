# BUSY internals: research notes

These notes record why the server is built the way it is. Confidence tags:
**[Confirmed]** = two independent sources, or a live-DB study plus the official schema;
**[Likely]** = one source, or a consistent inference; **[Unverified]** = kept out of report logic.

## Sources

| # | Source | What it is | Used for |
|---|---|---|---|
| S1 | [prateeknagwani/busy-extraction-poc](https://github.com/prateeknagwani/busy-extraction-poc) `FINDINGS.md` | Study of a live BUSY **SQL edition** database, with exact-arithmetic checks | Tables, sign convention, Tran2/Tran3 decode, FY databases |
| S2 | [timnirmal/busyProject](https://github.com/timnirmal/busyProject) `db2 Query.py` | **Full DDL of a real BUSY schema** (56 tables) | Column names: `Cancelled`, `VchCancelled`, `VchSeriesCode`, `ShortNar`, `VchOtherInfo.Narration1/2`, PKs |
| S3 | [vibhor1102/busy-whatsapp-bridge](https://github.com/vibhor1102/busy-whatsapp-bridge) `docs/BUSY_DATABASE_LEDGER_GUIDE.md` + code | Live **Access edition** study (99 tables, 15k vouchers), in production use | Access connection, the `(Cancelled = 0 OR Cancelled IS NULL)` filter, `Locks.CompanyName`, `MasterAddressInfo` fields |
| S4 | [thsarvesh/PRG-Files](https://github.com/thsarvesh/PRG-Files) `BusyGeneral.PRG` | FoxPro tool that **migrates BUSY data into another accounting package** | Voucher-type mapping (14 R, 19 P, 16 J, 15 C, 9/10/11 sales side, 2/3 purchase side); reserved group codes 101–129 |
| S5 | [imroz-jozy/stock-report-](https://github.com/imroz-jozy/stock-report-) | Stock report over BUSY | Item qty: closing = `Folio1.D1 + SUM(Tran2.Value1)`, so positive = stock in |
| S6 | [gitrkvhub/busy-ledger-viewer](https://github.com/gitrkvhub/busy-ledger-viewer) | Ledger viewer over a `.bds` file | `Folio1.D1` opening, `Tran2.Value1` > 0 = Credit, standard `.bds` password |
| S7 | [ManojMathuria/Easy-Publish-Prime…](https://github.com/ManojMathuria/Easy-Publish-Prime-To-Be-Live-Final) | VB6 app integrated through BUSY's COM DLL | `Busy2L21.CFixedInterface`, `SaveVchFromXML(13, <PurchaseOrder>)` |
| S8 | [busy.in FAQ](https://busy.in/faqs/third-party-services/integration/1/) | Official | No public API; integration goes through channel partners |

## 1. Integration route

| Route | Verdict |
|---|---|
| Official REST/XML API | Doesn't exist publicly [Confirmed S8] |
| COM DLL `Busy2L21.CFixedInterface` | Exists [Confirmed S7]. 32-bit, undocumented, one company per process, needs BUSY installed. Not used |
| **Direct read-only database access** | **Chosen.** Works for both editions, and is what every public integration above does |

## 2. Physical layout [Confirmed S1, S3, S6]

* Access edition: `<DATA>\COMP0001\db12024.bds`, one file per FY. The suffix is the **FY start year**.
* SQL edition: `BusyCOMP0001_db12024`. `BusyCOMP0001_db` without a year is a system DB with no `Tran1`.

## 3. Tables and columns

| Table | Key columns | Confidence |
|---|---|---|
| `Master1` | `Code` (PK), `MasterType`, `Name`, `Alias`, `PrintName`, `ParentGrp`, `CreationTime`, `ModificationTime` | Confirmed S1 S2 S3 |
| `Folio1` | (`MasterCode`, `MasterType`) → `D1` opening (account: **neg = Dr**; item: qty), `D3` item opening value | Confirmed S1 S3 S6 |
| `MasterAddressInfo` | `MasterCode`, `Address1-4`, `PINCode`, `TelNo`, `Mobile`, `Email`, `GSTNo`, `ITPAN`, `Contact` | Confirmed S2 S3 |
| `Tran1` | `VchCode` (PK), `VchType`, `Date`, `VchNo`, `VchSeriesCode`, `MasterCode1`, `VchAmtBaseCur`, `VchSalePurcAmt`, **`Cancelled`**, **`VchCancelled`**, `Status`, `ApprovalStatus`, `CreatedBy`, `ModifiedBy` | Confirmed S2 S3 |
| `Tran2` | `VchCode`, `SrNo`, `RecType` (1 account, 2 item), `MasterCode1`, `MasterCode2`, `Value1/2/3`, `D1..D32`, `ShortNar`, `VchType`, `Date`, `VchNo` | Confirmed S1 S2 S3 |
| `Tran3` | `VchCode`, `RecType` (4 = order lines), `RefCode`, `Method`, `DueDate`, `Value1-3`, `Balance1-3`, `NewRefAmount`, `Narration` | Order lines confirmed S1; bill refs unverified |
| `VchOtherInfo` | `VchCode`, `Narration1`, `Narration2`, `Transport`, `VehicleNo`, `GrNo`, `PurchaseBillNo` | Confirmed S2 |
| `BillingDet` | `VchCode`, billing party name, address, mobile, GST | Confirmed S2 S3 |
| `Locks` | `CompanyName` (newer versions), `MainDbVer` | Confirmed S3 (version-dependent, S2 lacks it) |
| `DailySum`, `Help1`, `Config`, `CheckList`, `MastFootPrint`, `VchGSTSumItemWise` | Caches, name index, settings, audit, GST | Seen S1 S2 S3 |

Columns differ between versions (e.g. `Narration1` appears on `Tran1` in S3 but not in S2). The
server therefore probes for them at runtime.

## 4. Sign conventions

* Account amounts: **negative = Debit, positive = Credit**, on `Tran2` RecType 1 and `Folio1.D1`. Each
  voucher's account lines sum to zero. [Confirmed S1 S6]
* Items: `Folio1.D1` positive = stock in hand [Confirmed S1]; `Tran2.Value1` **positive = inward**
  [Likely S5, consistent with S1]. The server also checks this against the database's own purchase
  vouchers.

## 5. Voucher types (`Tran1.VchType`)

| Code | Label | Evidence |
|---|---|---|
| 2 | Purchase | Confirmed S1 S4 |
| 3 | Sales Return | Confirmed S1 ("CN/" against customers) S4 (grouped with purchases = goods in, type R) |
| 9 | Sales | Confirmed S1 S4 |
| 10 | Purchase Return | Confirmed S1 ("DN/", party + Purchase legs) S4 (grouped with sales = goods out) |
| 11 | Sales Challan | Likely: S4 maps it to a challan; single source |
| 12 | Sales Order | Confirmed S1 |
| 13 | Purchase Order | Confirmed S7: BUSY's own COM API saves `<PurchaseOrder>` XML as type 13 |
| 14 | Receipt | Confirmed S4 (`book R`) + S1 data (bank debited) |
| 15 | Contra | Confirmed S1 S4 |
| 16 | Journal | Confirmed S1 ("JE/") S4 (`book J`) |
| 19 | Payment | Confirmed S4 (`book P`) + S1 ("PC/" petty cash) |
| 4 | Purchase Challan | Likely: mirrors 11, appears with 2 in S7 queries |
| 5, 8 | Stock Transfer / Stock Journal | Likely S1 (item-only, same item ±) |
| 17, 18 | Credit Note / Debit Note (no items) | Likely S1 |

S3's own constants table disagrees with S1/S4 on several codes, and contradicts itself, so it was
not used for labels.

## 6. Known gaps (contributions welcome)

1. `Tran3` bill-reference semantics. These would give true bill-wise outstanding with due dates.
2. Meaning of `Tran1.Status` / `ApprovalStatus`. Unapproved vouchers are currently included.
3. `Tran2` RecTypes beyond 1/2 (S3 sees 9 = charges; S4 uses 3 = discounts, 7 = items in another section).
4. Unit and material-centre links on item lines.
5. Codes 4, 5, 8, 11, 17, 18 need a second source.

If you have a BUSY install, an anonymised *Schema report* issue with `voucher-types` and
`master-type-summary` output closes these quickly.
