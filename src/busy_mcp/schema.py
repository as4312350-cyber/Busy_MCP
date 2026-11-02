"""Domain knowledge about BUSY's internal database.

BUSY does not publish its schema. Everything here comes from public
reverse-engineering of live BUSY databases, production integrations and a full
schema dump; docs/busy-internals.md lists the sources (S1..S11) and the rule
used for confidence: "confirmed" needs two independent sources.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------- masters

# Master1.MasterType -> (label, confidence)
MASTER_TYPE_INFO: dict[int, tuple[str, str]] = {
    1: ("Account Group", "confirmed"),
    2: ("Account", "confirmed"),
    5: ("Item Group", "confirmed"),
    6: ("Item", "confirmed"),
    8: ("Unit", "confirmed"),
    9: ("Voucher Series", "likely"),
    11: ("Material Centre", "confirmed"),
    13: ("Sale Type", "confirmed"),
    14: ("Purchase Type", "confirmed"),
    25: ("Tax Category", "confirmed"),
}
MASTER_TYPES: dict[int, str] = {k: v[0] for k, v in MASTER_TYPE_INFO.items()}
MT_GROUP, MT_LEDGER, MT_ITEM_GROUP, MT_ITEM, MT_UNIT = 1, 2, 5, 6, 8
MT_MATERIAL_CENTRE, MT_SALE_TYPE, MT_PURCHASE_TYPE, MT_TAX_CATEGORY = 11, 13, 14, 25


def master_type_label(code: int) -> str:
    info = MASTER_TYPE_INFO.get(code)
    if not info:
        return f"MasterType {code}"
    return info[0] if info[1] == "confirmed" else f"{info[0]} (inferred)"


# Known meanings of generic Master1 columns, per MasterType: column -> (meaning, confidence)
MASTER_FIELD_INFO: dict[int, dict[str, tuple[str, str]]] = {
    MT_ITEM: {
        "CM1": ("main unit", "confirmed"),
        "CM2": ("alternate unit", "likely"),
        "CM8": ("tax category", "likely"),
        "D1": ("alternate-unit conversion factor", "likely"),
        "D2": ("MRP", "likely"),
        "D3": ("sale price", "confirmed"),
        "D4": ("purchase price", "likely"),
        "HSNCode": ("HSN/SAC code", "likely"),
    },
    MT_LEDGER: {
        "I2": ("credit days", "likely"),
    },
}

# ------------------------------------------------------------------ vouchers

# Tran1.VchType -> (label, confidence)
VCH_TYPES: dict[int, tuple[str, str]] = {
    1: ("Opening Stock / Balance", "likely"),
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
VT_SALES_ORDER, VT_PURCHASE_ORDER, VT_RECEIPT, VT_PAYMENT = 12, 13, 14, 19

# Tran2.RecType
RT_LEDGER, RT_ITEM, RT_BILL_SUNDRY = 1, 2, 3
# Tran3.RecType (bill references, batches, order lines)
RT3_BATCH, RT3_ORDER_LINE = 2, 4
# Tran3.Method for bill references
METHOD_NEW_REF = 1


def vch_type_label(code: int | None, overrides: dict[int, str] | None = None) -> str:
    if code is None:
        return "Unknown"
    if overrides and code in overrides:
        return overrides[code]
    if code in VCH_TYPES:
        label, conf = VCH_TYPES[code]
        return label if conf == "confirmed" else f"{label} (inferred)"
    return f"VchType {code} (unmapped)"


# -------------------------------------------------------------- data dictionary

# table -> (category, description, confidence of the description)
TABLE_INFO: dict[str, tuple[str, str, str]] = {
    "Master1": ("Masters", "Every master: accounts, groups, items, units, material centres, sale types",
                "confirmed"),
    "Folio1": ("Masters", "Opening balances / opening stock per master (D1, D3)", "confirmed"),
    "MasterAddressInfo": ("Masters", "Party address, GSTIN, PAN, phone, mobile, e-mail", "confirmed"),
    "MasterSupport": ("Masters", "Additional per-master settings", "likely"),
    "Help1": ("Masters", "Name/alias search index over all masters", "confirmed"),
    "Help2": ("Masters", "Secondary lookup lists", "likely"),
    "MastFootPrint": ("Audit", "Master change footprints", "likely"),
    "Tran1": ("Vouchers", "Voucher headers: type, number, date, party, totals, cancelled flag, audit fields",
              "confirmed"),
    "Tran2": ("Vouchers", "Voucher lines: account postings (RecType 1), items (2), bill sundries (3)", "confirmed"),
    "Tran3": ("Vouchers", "References: bill-by-bill (Method 1 new / 2 against), batches (RecType 2), "
                          "order lines (RecType 4)", "likely"),
    "Tran4": ("Inventory", "Opening stock by material centre (RecType 0) and other period data", "likely"),
    "Tran5": ("Vouchers", "Secondary allocations per voucher line", "unverified"),
    "Tran6": ("Inventory", "Stock-type opening data", "unverified"),
    "Tran8": ("Taxes", "Statutory forms received/issued (pre-GST)", "likely"),
    "Tran9": ("Taxes", "Statutory form details per bill (pre-GST)", "likely"),
    "Tran10": ("Vouchers", "Voucher-series configuration data", "unverified"),
    "Tran11": ("Vouchers", "Additional voucher references", "unverified"),
    "Tran12": ("Audit", "Voucher print log (user, date, copies)", "confirmed"),
    "VchOtherInfo": ("Vouchers", "Narration, transport, vehicle, GR no, purchase bill no/date", "confirmed"),
    "BillingDet": ("Vouchers", "Billing party name/address/mobile/GSTIN printed on invoices", "confirmed"),
    "ItemDesc": ("Vouchers", "Item description lines per voucher item", "confirmed"),
    "PackingDetails": ("Vouchers", "Packing list per voucher", "confirmed"),
    "POSDet": ("Vouchers", "Point-of-sale payment split (cash, cheque, cards)", "confirmed"),
    "OrgSalePurc": ("Taxes", "Original sale/purchase reference for returns (taxable, tax)", "likely"),
    "VchGSTSumItemWise": ("Taxes", "GST per item line (ItemCode, VchDate, TaxRate, ...)", "confirmed"),
    "GSTInfo": ("Taxes", "GST registration / return data", "likely"),
    "GSTR1Info": ("Taxes", "GSTR-1 filing data", "likely"),
    "GSTR2AInfo": ("Taxes", "GSTR-2A downloaded purchase data", "likely"),
    "GSTR2BInfo": ("Taxes", "GSTR-2B ITC statement data", "likely"),
    "GSTR3BInfo": ("Taxes", "GSTR-3B summary data", "likely"),
    "TDS": ("Taxes", "TDS/TCS deductions: party, rate, amount, challan, quarter", "confirmed"),
    "VATInfo": ("Taxes", "Legacy VAT data", "confirmed"),
    "VchVATSum": ("Taxes", "Legacy VAT summary per voucher", "confirmed"),
    "VchVATSumItemWise": ("Taxes", "Legacy VAT per item line", "confirmed"),
    "STDet": ("Taxes", "Legacy service-tax details", "confirmed"),
    "Excise": ("Taxes", "Legacy excise data", "confirmed"),
    "TradingExcise": ("Taxes", "Legacy trading excise data", "confirmed"),
    "OEDDet": ("Taxes", "Legacy excise duty details", "confirmed"),
    "ItemParamDet": ("Inventory", "Batch / parameter-wise stock movements (C1 = batch no)", "likely"),
    "ItemSerialNo": ("Inventory", "Serial-number movements with warranty", "confirmed"),
    "ItemSNInstallDet": ("Inventory", "Installation details of serialised items", "confirmed"),
    "PhyStkSubDet": ("Inventory", "Physical stock verification details", "likely"),
    "JobFinishedRefs": ("Inventory", "Job-work finished goods references", "likely"),
    "AMCWarrDet": ("Inventory", "AMC / warranty contracts", "likely"),
    "BRSOpBal": ("Banking", "Bank reconciliation: uncleared opening entries (ClrDate)", "confirmed"),
    "DailySum": ("Banking", "BUSY's daily Dr/Cr summary cache per master", "likely"),
    "EmpLADet": ("Payroll", "Employee loans/advances instalments", "likely"),
    "CheckList": ("Audit", "Change log: type, code, action, time, user, computer", "confirmed"),
    "DeletedInfo": ("Audit", "Deleted vouchers/masters: who, when, amount, computer", "confirmed"),
    "EventLog": ("Audit", "User activity log", "confirmed"),
    "Config": ("System", "Company configuration (may contain stored credentials: blocked by default)", "confirmed"),
    "Locks": ("System", "Company / database version information", "confirmed"),
    "Patches": ("System", "Applied BUSY patches", "confirmed"),
    "RepColSize": ("System", "Report column layouts", "confirmed"),
    "RepOptValues": ("System", "Saved report options", "confirmed"),
    "VchTemplate": ("System", "Voucher templates", "likely"),
    "VchTempTagging": ("System", "Voucher template tagging", "likely"),
    "DailyMessage": ("System", "Daily messages shown in vouchers", "likely"),
    "ExternalData": ("System", "Links to external applications", "likely"),
    "DataSync": ("System", "Data synchronisation state", "likely"),
}

CATEGORY_ORDER = ("Masters", "Vouchers", "Inventory", "Taxes", "Banking", "Payroll", "Audit", "System", "Other")

# Columns that hold a Master1.Code (decoded to a name by generic readers)
_REF_COLUMN = re.compile(
    r"^(mastercode\d*|cm\d+|parentgrp|vchseriescode|itemcode|mccode|partycode|empcode|brokercode|"
    r"formcode|branchcode|[a-z]*acccode\d*)$",
    re.IGNORECASE,
)


def is_reference_column(name: str) -> bool:
    return bool(_REF_COLUMN.match(name))


# Column names whose values are never returned by generic readers
_SENSITIVE_COLUMN = re.compile(r"(pass(word)?|pwd|secret|token|otp|apikey|api_key|private)", re.IGNORECASE)


def is_sensitive_column(name: str) -> bool:
    return bool(_SENSITIVE_COLUMN.search(name))


SCHEMA_GUIDE = r"""
# BUSY database guide (for `query-database` and `read-table`)

Prefer the dedicated tools. `catalog` lists every table with its meaning; `read-table` reads any of
them without SQL and decodes master codes to names automatically.

BUSY stores each company + financial year as ONE database:
  * Access edition : <DATA>\COMP0001\db12024.bds   (MS-Access/Jet file)
  * SQL edition    : database BusyCOMP0001_db12024 on the BUSY SQL Server instance
  The 4-digit suffix is the FY START year (db12024 = FY 2024-25).
Generic "wide table" design: D1..Dn / I1..In / B1..Bn / C1..Cn / CM1..CMn change meaning per type.
Columns differ between BUSY versions — check with `describe-table` first.

## Master1 — every master. PK Code (unique within one database)
  MasterType: 1 Account Group, 2 Account, 5 Item Group, 6 Item, 8 Unit, 11 Material Centre,
  13 Sale Type, 14 Purchase Type, 25 Tax Category (9 Voucher Series, likely). ParentGrp = parent Code.
  Item: CM1 main unit, CM2 alt unit, CM8 tax category, D2 MRP, D3 sale price, D4 purchase price, HSNCode.
  Account: I2 credit days (likely). DeactiveMaster = inactive flag (newer versions).
## Folio1 — (MasterCode, MasterType): D1 opening (account: NEGATIVE = Dr; item: qty), D3 item opening value
## MasterAddressInfo — MasterCode: Address1-4, PINCode, TelNo, Mobile, WhatsAppNo, Email, GSTNo, ITPAN
## Tran1 — voucher header, PK VchCode
  VchType Date VchNo VchSeriesCode MasterCode1 (party) MasterCode2 (material centre) CM1 (sale/purchase type)
  VchAmtBaseCur (total incl. tax) VchSalePurcAmt (goods value excl. tax) StockUpdationDate
  Cancelled / VchCancelled — ALWAYS exclude: (Cancelled = 0 OR Cancelled IS NULL)
  CreatedBy CreationTime ModifiedBy ModificationTime
  VchType: 2 Purchase, 3 Sales Return, 9 Sales, 10 Purchase Return, 12 Sales Order, 13 Purchase Order,
           14 Receipt, 15 Contra, 16 Journal, 19 Payment (likely: 1 Opening, 4 Purchase Challan,
           5 Stock Transfer, 8 Stock Journal, 11 Sales Challan, 17 Credit Note, 18 Debit Note)
## Tran2 — voucher lines (VchCode, SrNo); also VchType, Date, VchNo
  RecType 1 = account : MasterCode1 account, Value1 signed amount (NEGATIVE = Dr), ClrDate (bank clearing)
  RecType 2 = item    : MasterCode1 item, MasterCode2 material centre, Value1 signed qty (POSITIVE = in),
                        Value3 value excl. tax, D4 list price, D9 discount %, D2/D3 net rate incl. tax
  RecType 3 = bill sundry (freight, discount, ...). Some versions also carry items in RecType 7 / 20.
  ShortNar = line narration. Account lines of a voucher sum to zero.
## Tran3 — references: Method 1 = new ref, 2 = against ref; No = bill/batch no; Date; DueDate; Value1
  RecType 2 = batch (No batch, DueDate expiry, MfgDate, Balance1-3 rates); RecType 4 = order lines.
## Others: VchOtherInfo (narration, transport), BillingDet, ItemDesc, VchGSTSumItemWise (GST per item),
  TDS, ItemParamDet (batches: C1), ItemSerialNo, Tran4 (MC opening stock, RecType 0), BRSOpBal,
  CheckList / DeletedInfo / EventLog (audit), POSDet, DailySum, Locks (company name).

## SQL dialect
  * Access = Jet SQL: TOP n, IIF(), no CASE / CTE, parenthesise multiple JOINs, #2024-04-01# dates, [Date].
  * SQL edition = T-SQL: TOP n, CASE/IIF, CTEs, '2024-04-01' dates.
  * Always use TOP — Tran2 can have millions of rows.
"""
