"""What is known about BUSY's internal database schema.

BUSY does not publish its schema. Everything here comes from public
reverse-engineering work against live BUSY databases (see
docs/BUSY_INTERNALS.md for sources and confidence levels). Labels that are
inferred rather than confirmed are marked so the AI can say so.
"""

from __future__ import annotations

# Master1.MasterType
MASTER_TYPES: dict[int, str] = {
    1: "Account Group",
    2: "Account (Ledger)",
    5: "Item Group (Stock Group)",
    6: "Item (Stock Item)",
}
MT_GROUP, MT_LEDGER, MT_ITEM_GROUP, MT_ITEM = 1, 2, 5, 6

# Tran1.VchType -> (label, confidence)
# confirmed = seen in real data with matching voucher-number prefixes / legs
# likely    = inferred from accounting signs or a real integration's code
VCH_TYPES: dict[int, tuple[str, str]] = {
    2: ("Purchase", "confirmed"),
    3: ("Sales Return (Credit Note with items)", "confirmed"),
    5: ("Stock Transfer / Stock Journal", "likely"),
    8: ("Stock Journal", "likely"),
    9: ("Sales", "confirmed"),
    10: ("Purchase Return (Debit Note with items)", "confirmed"),
    12: ("Sales Order", "confirmed"),
    13: ("Purchase Order", "confirmed"),
    14: ("Receipt", "likely"),
    15: ("Contra", "confirmed"),
    16: ("Journal", "confirmed"),
    17: ("Credit Note (without items)", "likely"),
    18: ("Debit Note (without items)", "likely"),
    19: ("Payment", "likely"),
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
  * Access edition : <DATA>\COMP0001\db12024.bds   (MS-Access/Jet file, password-protected)
  * SQL edition    : database  BusyCOMP0001_db12024 on the BUSY SQL Server instance
  The 4-digit suffix is the FY START year (db12024 = FY 2024-25, 1-Apr-2024 .. 31-Mar-2025).
  BusyCOMP0001_db (no year) is a company/system database, not voucher data.

The schema is a generic "wide table" design. Meaning of D1..Dn / I1..In / B1..Bn / C1..Cn
columns changes per MasterType / RecType and is mostly undocumented.

## Master1 — every master (one row per account, group, item, unit, etc.)
  Code (PK, unique within one database)  MasterType  Name  Alias  PrintName  ParentGrp
  MasterType: 1 = Account Group, 2 = Account/Ledger, 5 = Item Group, 6 = Item.
  ParentGrp = Code of the parent group (Account Group for 1/2, Item Group for 5/6); 0 = top level.
  Other MasterType values exist (units, material centres, voucher series, tax categories, ...)
  — use `master-type-summary` to see them.
  Item master: D3 has been observed to hold the item's list price.

## Folio1 — opening balances, key (MasterCode, MasterType)
  Ledger (MasterType 2): D1 = opening balance; NEGATIVE = Debit, POSITIVE = Credit.
  Item   (MasterType 6): D1 = opening quantity, D3 = opening value (positive = stock in hand).

## MasterAddressInfo — party details, key MasterCode -> Master1.Code
  Address lines, GSTNo, contact fields, etc.

## Tran1 — voucher header (one row per voucher)
  VchCode (PK)  VchType  VchNo  Date  MasterCode1 (party / main account)
  VchAmtBaseCur  = voucher total incl. tax (matches BUSY screen)
  VchSalePurcAmt = goods subtotal excl. tax (NOT the invoice total)
  VchType codes: 2 Purchase, 3 Sales Return, 9 Sales, 10 Purchase Return, 12 Sales Order,
  13 Purchase Order, 15 Contra, 16 Journal (confirmed); 14 Receipt, 19 Payment,
  17 Credit Note, 18 Debit Note, 5/8 Stock Journal/Transfer (inferred).
  Use `voucher-types` to see codes actually used in the open database with sample numbers.

## Tran2 — accounting + inventory lines, key VchCode -> Tran1.VchCode
  RecType 1 = ledger line : MasterCode1 = ledger Code, Value1 = signed amount (NEGATIVE = Dr, POSITIVE = Cr)
  RecType 2 = item line   : MasterCode1 = item Code, Value1 (= Value2) = signed qty, Value3 = signed value excl. tax
            item extras   : D4 = MRP/list price, D9 = discount %, D2 = D3 = net rate incl. tax, D6 = net rate excl. tax
  Also carries Date, VchNo, SrNo.
  Every voucher's RecType 1 lines sum to zero (double entry).

## Tran3 — order lines & references, key VchCode
  RecType 4 = Sales/Purchase Order item lines: MasterCode1 = item, Value1 = qty, Value3 = rate,
  NewRefAmount = line amount, ItemSrNo = line number.
  Other RecTypes hold bill-wise references (RefCode, Method, DueDate, Balance1..3) — unverified.

## Other tables seen
  VchGSTSumItemWise (GST per item line: ItemCode, VchDate, TaxRate, TaxRate1, ...),
  VchOtherInfo, DailySum (daily balance cache), MastFootPrint (audit), Tran7/Tran10/Tran12.

## SQL dialect tips
  * Access edition = Jet SQL: use TOP n, IIF(), no CASE, no CTE/WITH, parenthesise multiple JOINs:
      SELECT ... FROM (Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode)
                      INNER JOIN Master1 m ON m.Code = t2.MasterCode1
    Date literals: #2024-04-01#.
  * SQL edition = T-SQL: TOP n, IIF/CASE, CTEs all fine. Date literals: '2024-04-01'.
  * Always add TOP to exploratory queries — Tran2 can have millions of rows.
"""
