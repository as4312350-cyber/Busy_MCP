"""What is known about BUSY's internal database schema.

BUSY does not publish its schema. Everything here comes from public
reverse-engineering of live BUSY databases plus a full schema dump of a real
BUSY install; see docs/busy-internals.md for sources and confidence levels.
"""

from __future__ import annotations

# Master1.MasterType
MASTER_TYPES: dict[int, str] = {
    1: "Account Group",
    2: "Account (Ledger)",
    5: "Item Group",
    6: "Item",
    8: "Unit",
}
MT_GROUP, MT_LEDGER, MT_ITEM_GROUP, MT_ITEM = 1, 2, 5, 6

# Tran1.VchType -> (label, confidence)
#   confirmed : two independent sources agree (live-DB study + a production migration tool / integration)
#   likely    : one source, or consistent inference from accounting signs
VCH_TYPES: dict[int, tuple[str, str]] = {
    2: ("Purchase", "confirmed"),
    3: ("Sales Return", "confirmed"),
    4: ("Purchase Challan", "likely"),
    5: ("Stock Transfer", "likely"),
    8: ("Stock Journal", "likely"),
    9: ("Sales", "confirmed"),
    10: ("Purchase Return", "confirmed"),
    11: ("Sales Challan", "likely"),
    12: ("Sales Order", "confirmed"),
    13: ("Purchase Order", "confirmed"),
    14: ("Receipt", "confirmed"),
    15: ("Contra", "confirmed"),
    16: ("Journal", "confirmed"),
    17: ("Credit Note", "likely"),
    18: ("Debit Note", "likely"),
    19: ("Payment", "confirmed"),
}
VT_PURCHASE, VT_SALES_RETURN, VT_SALES, VT_PURCHASE_RETURN = 2, 3, 9, 10

# Tran2.RecType
RT_LEDGER, RT_ITEM = 1, 2
# Tran3.RecType
RT3_ORDER_LINE = 4


def vch_type_label(code: int | None, overrides: dict[int, str] | None = None) -> str:
    if code is None:
        return "Unknown"
    if overrides and code in overrides:
        return overrides[code]
    if code in VCH_TYPES:
        label, conf = VCH_TYPES[code]
        return label if conf == "confirmed" else f"{label} (inferred)"
    return f"VchType {code} (unmapped)"


SCHEMA_GUIDE = r"""
# BUSY database schema guide (for writing SQL with `query-database`)

BUSY stores each company + financial year as ONE database:
  * Access edition : <DATA>\COMP0001\db12024.bds   (MS-Access/Jet file)
  * SQL edition    : database  BusyCOMP0001_db12024 on the BUSY SQL Server instance
  The 4-digit suffix is the FY START year (db12024 = FY 2024-25, 1-Apr-2024 .. 31-Mar-2025).
Generic "wide table" design: D1..Dn / I1..In / B1..Bn / C1..Cn / CM1..CMn change meaning per type.
Columns differ slightly between BUSY versions — check with `describe-table` first.

## Master1 — every master. PK Code (unique within one database)
  Code  MasterType  Name  Alias  PrintName  ParentGrp (0 = top level)  CreationTime  ModificationTime
  MasterType: 1 Account Group, 2 Account, 5 Item Group, 6 Item, 8 Unit (others: series, taxes, ...).
  Built-in account groups have reserved Codes 101-129.
## Folio1 — opening balances. Key (MasterCode, MasterType)
  Account: D1 = opening, NEGATIVE = Dr, POSITIVE = Cr.   Item: D1 = opening qty, D3 = opening value.
## MasterAddressInfo — key MasterCode: Address1-4, PINCode, TelNo, Mobile, Email, GSTNo, ITPAN, Contact
## Tran1 — voucher header. PK VchCode
  VchType  Date  VchNo  VchSeriesCode (-> Master1.Code)  MasterCode1 (party/main account)
  VchAmtBaseCur (total incl. tax)  VchSalePurcAmt (goods value excl. tax)
  Cancelled / VchCancelled (boolean — ALWAYS exclude: (Cancelled = 0 OR Cancelled IS NULL))
  Status  ApprovalStatus  CreatedBy  CreationTime  ModifiedBy  ModificationTime
  VchType: 2 Purchase, 3 Sales Return, 9 Sales, 10 Purchase Return, 12 Sales Order,
           13 Purchase Order, 14 Receipt, 15 Contra, 16 Journal, 19 Payment
           (likely: 4 Purchase Challan, 5 Stock Transfer, 8 Stock Journal, 11 Sales Challan,
            17 Credit Note, 18 Debit Note)
## Tran2 — voucher lines. Key (VchCode, SrNo, ...). Also carries VchType, Date, VchNo
  RecType 1 = account line : MasterCode1 = account, Value1 = signed amount (NEGATIVE = Dr, POSITIVE = Cr)
  RecType 2 = item line    : MasterCode1 = item, Value1 = signed qty (POSITIVE = stock in), Value3 = value excl. tax
                             D4 = list price, D9 = discount %, D2/D3 = net rate incl. tax, D6 = net rate excl. tax
  ShortNar = line narration.  Ledger lines of one voucher sum to zero.
## Tran3 — references. RecType 4 = order item lines (Value1 qty, Value3 rate, NewRefAmount amount);
  other RecTypes = bill-wise references (RefCode, Method, DueDate, Balance1-3, Narration).
## VchOtherInfo — key VchCode: Narration1, Narration2, Transport, VehicleNo, GrNo, PurchaseBillNo/Date
## BillingDet — key VchCode: billing party name/address/mobile/GST on invoices
## Others: VchGSTSumItemWise (GST per item line), DailySum (daily Dr/Cr per master), Locks (company/version),
  Config, Help1 (name index), CheckList/MastFootPrint (audit), ItemDesc, Tran4..Tran12.

## SQL dialect tips
  * Access edition = Jet SQL: TOP n, IIF(), no CASE, no CTE, parenthesise multiple JOINs:
      SELECT ... FROM (Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode)
                      INNER JOIN Master1 m ON m.Code = t2.MasterCode1
    Date literals #2024-04-01#. Quote the Date column as [Date].
  * SQL edition = T-SQL: TOP n, CASE/IIF, CTEs fine. Date literals '2024-04-01'.
  * Always use TOP — Tran2 can have millions of rows.
"""
