# BUSY internals: research notes

These notes explain why the server reads BUSY the way it does.

**Confidence rule:**
* **[Confirmed]:** two independent sources, or a live-database study plus the official schema dump.
* **[Likely]:** a single strong source, or a consistent inference.
* **[Unverified]:** kept out of report logic.

Facts that vary between BUSY versions are **probed at runtime** rather than assumed (see
[architecture.md](architecture.md#runtime-detection-instead-of-assumptions)).

## Sources

| # | Source | What it is |
|---|---|---|
| S1 | [prateeknagwani/busy-extraction-poc](https://github.com/prateeknagwani/busy-extraction-poc) `FINDINGS.md` | Study of live BUSY **SQL edition** databases (three companies), with exact-arithmetic checks |
| S2 | [timnirmal/busyProject](https://github.com/timnirmal/busyProject) `db2 Query.py` | **Full DDL of a real BUSY schema** (56 tables) |
| S3 | [vibhor1102/busy-whatsapp-bridge](https://github.com/vibhor1102/busy-whatsapp-bridge) | Live **Access edition** study (99 tables) and production code |
| S4 | [thsarvesh/PRG-Files](https://github.com/thsarvesh/PRG-Files) `Busy*.PRG` | FoxPro tools that **migrate BUSY data** (general, pharma, FMCG, garment) into another package |
| S5 | [imroz-jozy/stock-report-](https://github.com/imroz-jozy/stock-report-) | Stock report over BUSY |
| S6 | [gitrkvhub/busy-ledger-viewer](https://github.com/gitrkvhub/busy-ledger-viewer) | Ledger viewer over a `.bds` file |
| S7 | [ManojMathuria/Easy-Publish-Prime…](https://github.com/ManojMathuria/Easy-Publish-Prime-To-Be-Live-Final) | VB6 app integrated through BUSY's COM DLL |
| S8 | [busy.in FAQs](https://busy.in/faqs/third-party-services/integration/1/) | Official: no public API; feature FAQs (bill-by-bill, PDC, batches, serials, BOM) |
| S9 | [softwaredlpm/wasender](https://github.com/softwaredlpm/wasender) `busy_db_bridge.ps1` | **Production bridge** reading BUSY (Access and SQL): balances, bills, PDC, stock by godown, batches |
| S10 | [goks/Record-Matcher](https://github.com/goks/Record-Matcher) | Bank reconciliation tool over BUSY (`CLRDATE`) |
| S11 | [goks/Price-List-Query-Backend](https://github.com/goks/Price-List-Query-Backend) | Price-list tool over BUSY masters |
| S12 | [koffykraft/tagro-echo-os](https://github.com/koffykraft/tagro-echo-os) | Scripts that copy and read a live BUSY `.bds` |
| S13 | [push04/LEVITATE](https://github.com/push04/LEVITATE) `agent/busy_levitate_agent.py` | Agent reading BUSY's `.bds` over ODBC |

## 1. Integration route

There's no public API [Confirmed S8]. The COM DLL `Busy2L21.CFixedInterface` exists [Confirmed S7], but it
is 32-bit, undocumented, opens one company per process and needs BUSY installed. **Direct read-only database
access** works for both editions and is what every public integration does.

## 2. Physical layout [Confirmed S1, S3, S6, S9]

* **Access edition:** `<DATA>\COMP0001\db12024.bds`, one file per FY. The suffix is the **FY start year**.
* **SQL edition:** `BusyCOMP0001_db12024`. `BusyCOMP0001_db` without a year is a system database.

### File format and drivers

A `.bds` file is a Microsoft Access database whose default password is `ILoveMyINDIA` [Confirmed S2 S3 S7 S9].
Integrations pass it unbraced (`PWD=ILoveMyINDIA`) [Confirmed S2 S3 S13]: the Access driver would keep braces as
part of the password.

Access has two file formats. Bytes 0–20 tell them apart, and `busy-mcp doctor` prints which one a company uses
[Confirmed: [mdbtools](https://github.com/mdbtools/mdbtools/blob/96d6880ccf7b67fdb468c3546f2b815aabc91510/src/libmdb/file.c),
[mimetype](https://github.com/gabriel-vasile/mimetype/blob/4984e3fbe2b53fce64763fb0ab5d0da43f2751aa/internal/magic/database.go),
[LibRed format notes](https://github.com/CirrusRedOrg/EntityFrameworkCore.Jet/blob/164e449e75327d292d4a627fbe35d22f10b76e4b/src/LibRed/docs/format/appendix-structures.md),
`file` 5.45]:

| Format | Bytes 0–20 | Read by |
|---|---|---|
| `.mdb` (Access 97–2003) | `00 01 00 00`, `Standard Jet DB`, `00`, version `00` (Jet 3) or `01` (Jet 4) | Windows' built-in Jet driver **and** the Access Database Engine (ACE) |
| `.accdb` (Access 2007+) | `00 01 00 00`, `Standard ACE DB`, `00`, version `02` (ACE 12) or later | the Access Database Engine only |

Jet rejects an `.accdb` file with `Unrecognized database format` [Likely; the `access-ace` CI job checks it].
That is Access error -1206 [Likely: LibRed notes], a number that stays the same whatever the Windows language, so
busy-mcp recognises either one.

What BUSY and its integrations use:

* **At least some BUSY versions write `.mdb`** [Likely S7]: a production VB6 app opens
  [`COMP0006\db12022.bds` with `Microsoft.Jet.OLEDB.4.0`](https://github.com/ManojMathuria/Easy-Publish-Prime-To-Be-Live-Final/blob/3066e717dbd8064cff68ea3e3439db0cb9c6f751/Form/SalesVoucher.frm),
  which can't read `.accdb`.
* **No source shows an `.accdb`-format `.bds`** [Unverified that any BUSY version writes one]. S2
  [copies `db.bds` to `db12020.accdb`](https://github.com/timnirmal/busyProject/blob/40a04b64c3cc3ea073e914f152dc61e76e919d75/LogGen.py),
  but that only renames the file. `doctor` output from real installs settles this.
* **Most integrations read BUSY through the Access Database Engine** [Confirmed S2 S3 S9 S13]:
  * S3 uses the ODBC driver `Microsoft Access Driver (*.mdb, *.accdb)`, and its
    [README](https://github.com/vibhor1102/busy-whatsapp-bridge/blob/a60dfa81e0d79443dcd792623ecc24590c8b2822/README.md)
    has users check for it in the 32-bit ODBC panel (`%windir%\syswow64\odbcad32.exe`) with 32-bit Python.
  * S9 uses [`Provider=Microsoft.ACE.OLEDB.12.0` with `Jet OLEDB:Database Password`](https://github.com/softwaredlpm/wasender/blob/ed3f7d9f361d180b03529e0dd9407e71c1f7a1bd/busy_db_bridge.ps1).
  * S2 and [S13](https://github.com/push04/LEVITATE/blob/1ba01afb555ef9590eb6e65b2de318c305f537df/agent/busy_levitate_agent.py)
    use the same ODBC driver as S3.
  * S12 [checks for an ACE 12.0/16.0 *or* Jet 4.0 OLE DB provider](https://github.com/koffykraft/tagro-echo-os/blob/78c423e7aa9c38fc95fab5533639fc1b8e84dfc0/scripts/refresh_current_fy_from_td_latest.ps1)
    before reading `db12026.bds` [Likely: Jet is enough there].
* One S3 test script opens a file with another password (`pwd=bsw`) [Unverified]. If yours differs, set
  `BUSY_ACCESS_PASSWORD`.

So busy-mcp uses the ACE driver when it is installed and Windows' Jet driver otherwise. Either reads `.mdb`;
`.accdb` needs ACE. CI reads the test company through each one on 32-bit Python: Jet with `.mdb`, and ACE with
both `.mdb` and `.accdb`.

## 3. Masters

`Master1.MasterType` values:

| Type | Meaning | Evidence |
|---|---|---|
| 1 / 2 | Account group / account | Confirmed S1 S3 S4 |
| 5 / 6 | Item group / item | Confirmed S1 S4 S11 |
| 8 | Unit | Confirmed S4 S11 |
| 11 | Material centre (godown) | Confirmed S9 S7 |
| 13 / 14 | Sale type / purchase type (e.g. `L/GST-18%`), referenced by `Tran1.CM1` | Confirmed S4 S7 |
| 25 | Tax category (e.g. `GST 18%`) | Confirmed S4 S11 |
| 9 | Voucher series | Likely S3 |

Other master facts:
* Built-in account groups use reserved codes 101–129 [Likely S4].
* `ParentGrp` is the parent's code, and 0 means top level [Confirmed].
* `DeactiveMaster` is the inactive flag in newer versions [S11].

Item master fields [S4 pharma tool]:

| Column | Meaning | Confidence |
|---|---|---|
| `CM1` | Main unit (code of a type-8 master) | Confirmed with S1 |
| `CM2` | Alternate unit | Likely |
| `CM8` | Tax category (code of a type-25 master) | Likely |
| `D1` | Alternate-unit conversion factor | Likely |
| `D2` | MRP | Likely |
| `D3` | Sale price | Confirmed with S1 |
| `D4` | Purchase price | Likely |
| `HSNCode` | HSN/SAC code | Likely |

Account master: `I2` = credit days [Likely S3].

## 4. Balances and sign conventions

* **Account amounts:** negative = Debit, positive = Credit, in `Tran2` RecType 1 and `Folio1.D1`. Each
  voucher's account lines sum to zero. [Confirmed S1 S6]
* **Item opening:** `Folio1.D1` is quantity, `D3` is value; positive means stock in hand. [Confirmed S1]
* **Item movement:** `Tran2.Value1` positive means inward [Confirmed S5 + S1]. For stock journals, positive
  is "items generated" and negative is "items consumed" [S1, Folio 12]. The server still checks this on each
  database's own purchases.

## 5. Vouchers

* **`Tran1` columns** [Confirmed S2 S3]: `VchCode`, `VchType`, `Date`, `VchNo`, `VchSeriesCode`,
  `MasterCode1` (party), `MasterCode2` (material centre, S7), `CM1` (sale/purchase type), `VchAmtBaseCur`,
  `VchSalePurcAmt`, `StockUpdationDate`, `Cancelled`, `VchCancelled`, `Status`, `ApprovalStatus`, `CreatedBy`,
  `CreationTime`, `ModifiedBy`, `ModificationTime`.
* **Cancelled vouchers** must be excluded with `(Cancelled = 0 OR Cancelled IS NULL)` [Confirmed S3 S9].
* **`Tran2.RecType`:**
  * 1 = account line [Confirmed]
  * 2 = item line [Confirmed]
  * 3 = bill sundry / discount [Likely S4]
  * 7 and 20 = item lines in some versions [S4, S9]. The server counts them only if they aren't mirrors of
    RecType 2 lines.
* **`Tran2.MasterCode2`** on item lines = material centre [Likely S9].
* **`Tran2.ClrDate`** = bank clearing date [Likely S10 + DDL]. Ancient dates mean "not cleared" (S10 treats
  years before 2020 as empty).
* **Narration:** `VchOtherInfo.Narration1/2` (S2) or `Tran1.Narration1` (S3). Line narration is
  `Tran2.ShortNar`.

## 6. References (`Tran3`)

* **Bill-by-bill** [Likely S9]: `Method` 1 = new reference (bill), 2 = against reference (settlement).
  Other columns: `No` = bill number, `Date`, `DueDate`, `Value1` signed like `Tran2`. The server links bills
  to settlements by `RefCode` or by bill number, picking whichever leaves fewer unmatched settlements, and
  reconciles the result with the ledger balance.
* **Batches** [Likely S4]: RecType 2, with `No` = batch, `DueDate` = expiry, `MfgDate`, `Balance1/2/3` =
  purchase rate / sale rate / MRP. Batch quantities come from `ItemParamDet` (`C1` = batch, `Value1` = qty,
  `MCCode` = godown) [Likely S9].
* **Order lines** [Confirmed S1]: RecType 4, with `MasterCode1` = item, `MasterCode2` = party, `Value1` =
  qty, `Value3` = rate. Executions are linked by `RefCode` [Likely].
* **Post-dated cheques** [Likely S9]: receipts and payments whose `Tran1.StockUpdationDate` is after the
  reporting date.

## 7. Voucher types (`Tran1.VchType`)

| Code | Label | Evidence |
|---|---|---|
| 2 | Purchase | Confirmed S1 S4 |
| 3 | Sales Return | Confirmed S1 S4 |
| 9 | Sales | Confirmed S1 S4 |
| 10 | Purchase Return | Confirmed S1 S4 |
| 12 | Sales Order | Confirmed S1 |
| 13 | Purchase Order | Confirmed S7 (BUSY's own COM API) |
| 14 | Receipt | Confirmed S4 + S1 data |
| 15 | Contra | Confirmed S1 S4 |
| 16 | Journal | Confirmed S1 S4 |
| 19 | Payment | Confirmed S4 + S1 |
| 1 | Opening stock / balance | Likely S4 (`vchtype=1` with `VchCode=0`) |
| 4 / 11 | Purchase / sales challan | Likely S4 S7 |
| 5 / 8 | Stock transfer / stock journal | Likely S1 |
| 17 / 18 | Credit / debit note without items | Likely S1 |

S3's own constants contradict S1/S4 and themselves, so they were not used.

## 8. Other tables [Confirmed column names S2]

| Table | Columns |
|---|---|
| `TDS` | party, TDS account, rate, amount, challan, quarter |
| `DeletedInfo` | who/when/amount of deletions |
| `CheckList` / `EventLog` | change log and user activity |
| `ItemSerialNo` | serial movements, warranty |
| `BRSOpBal` | uncleared bank items brought forward |
| `POSDet` | point-of-sale payment split |
| `BillingDet` | billing details |
| `Config` | settings; **may hold GST portal credentials in plain text** [S3], so it is blocked by default |

## 9. Open questions

1. `Tran1.Status` / `ApprovalStatus` / `Stamp` semantics. Unapproved vouchers are currently included.
2. Whether BUSY posts post-dated cheques to ledgers before their effective date.
3. Tables for cost centres and budgets in current versions.
4. Second sources for codes 1, 4, 5, 8, 11, 17, 18 and for `MasterType` 9.

If you have a BUSY install, an anonymised *Schema report* issue with `voucher-types`, `master-type-summary`
and `catalog` output closes these quickly.
