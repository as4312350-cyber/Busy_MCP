"""A small company in BUSY's exact table layout, column names and sign conventions
(taken from a real BUSY schema dump), so every report can be tested without
Windows or a BUSY licence.

Story (FY 2024-25):
  * Capital 1,00,000; HDFC Bank 80,000; Cash 20,000; Widget A opening 10 @ Main Store.
  * P/1   purchase 50 Widget A from Gupta (5,900 incl. GST), batch B1 (expiry 30-Jun-2025).
  * S/1   sale of 40 Widget A to Sharma (11,800 incl. GST), bill due 04-Jun-2024.
  * R/1   receipt 8,000 from Sharma into HDFC (cleared 05-Jun-2024), against bill S/1.
  * PY/1  rent 3,000 paid in cash.  JE/1 rent 500 (journal against cash).
  * CN/1  sales return 4 Widget A (1,180) against bill S/1.
  * SO/1  sales order 5 Widget B @ 200; SC/1 challan executes 2 of them.
  * ST/1  stock transfer 5 Widget A Main Store -> Branch Godown.
  * SJ/1  stock journal produces 3 Widget B (RecType 20 line, serials SN-1..SN-3).
  * PY/2  payment 5,900 to Gupta by HDFC cheque, not yet cleared by the bank.
  * S/2   CANCELLED sale of 99,999 (must never be counted).
  * R/2   post-dated cheque receipt (effective 15-Apr-2025), no postings yet.
Expected results are in EXPECTED and asserted in the tests.
"""

from __future__ import annotations

import sqlite3

MAIN, BRANCH = 1101, 1102
SALE_TYPE, PURC_TYPE, TAX_CAT, UNIT, SERIES = 1301, 1401, 2501, 801, 901

GROUPS = [  # Code, Name, ParentGrp  (BUSY reserves codes 101-129 for built-in groups)
    (101, "Capital Account", 0), (102, "Current Assets", 0), (103, "Current Liabilities", 0),
    (104, "Revenue Accounts", 0), (110, "Sundry Debtors", 102), (111, "Bank Accounts", 102),
    (112, "Cash-in-hand", 102), (120, "Sundry Creditors", 103), (121, "Duties & Taxes", 103),
    (130, "Sale", 104), (131, "Purchase", 104), (132, "Expenses (Indirect/Admn.)", 104),
]
LEDGERS = [  # Code, Name, ParentGrp, Folio1.D1 opening (negative = Dr), I2 credit days
    (201, "Capital", 101, 100000.0, 0), (202, "HDFC Bank", 111, -80000.0, 0), (203, "Cash", 112, -20000.0, 0),
    (204, "Sharma Traders", 110, 0.0, 30), (205, "Gupta Suppliers", 120, 0.0, 0), (206, "Sales", 130, 0.0, 0),
    (207, "Purchase", 131, 0.0, 0), (208, "Rent", 132, 0.0, 0), (209, "Output GST", 121, 0.0, 0),
    (210, "Input GST", 121, 0.0, 0), (211, "TDS Payable", 121, 0.0, 0),
]
OTHER_MASTERS = [  # Code, MasterType, Name
    (MAIN, 11, "Main Store"), (BRANCH, 11, "Branch Godown"), (SALE_TYPE, 13, "L/GST-18%"),
    (PURC_TYPE, 14, "L/GST-18% Purchase"), (TAX_CAT, 25, "GST 18%"), (UNIT, 8, "Pcs."), (SERIES, 9, "Main"),
    (501, 5, "Electronics"),
]
ITEMS = [  # Code, Name, Folio1 opening qty, opening value, MRP, sale price, purchase price, HSN
    (601, "Widget A", 10.0, 1000.0, 350.0, 300.0, 100.0, "8471"),
    (602, "Widget B", 0.0, 0.0, 250.0, 200.0, 120.0, "8473"),
]

# VchCode, VchType, VchNo, Date, party, total, goods, cancelled, CM1, MC, StockUpdationDate, ModifiedBy, ModTime
VOUCHERS = [
    (1, 2, "P/1", "2024-04-10", 205, 5900.0, 5000.0, 0, PURC_TYPE, MAIN, "2024-04-10", None, None),
    (2, 9, "S/1", "2024-05-05", 204, 11800.0, 10000.0, 0, SALE_TYPE, MAIN, "2024-05-05", "ravi", "2024-05-06 11:00:00"),
    (3, 14, "R/1", "2024-06-01", 204, 8000.0, 0.0, 0, 0, 0, "2024-06-01", None, None),
    (4, 19, "PY/1", "2024-06-15", 208, 3000.0, 0.0, 0, 0, 0, "2024-06-15", None, None),
    (5, 3, "CN/1", "2024-07-01", 204, 1180.0, 1000.0, 0, SALE_TYPE, MAIN, "2024-07-01", None, None),
    (6, 12, "SO/1", "2024-07-10", 204, 1180.0, 1000.0, 0, 0, MAIN, "2024-07-10", None, None),
    (7, 19, "PY/2", "2025-03-20", 205, 5900.0, 0.0, 0, 0, 0, "2025-03-20", None, None),
    (8, 9, "S/2", "2024-08-01", 204, 99999.0, 99999.0, 1, SALE_TYPE, MAIN, "2024-08-01", None, None),
    (9, 16, "JE/1", "2024-09-01", 0, 0.0, 0.0, 0, 0, 0, "2024-09-01", None, None),
    (10, 5, "ST/1", "2024-09-10", 0, 0.0, 0.0, 0, 0, MAIN, "2024-09-10", None, None),
    (11, 8, "SJ/1", "2024-09-15", 0, 0.0, 0.0, 0, 0, MAIN, "2024-09-15", None, None),
    (12, 11, "SC/1", "2024-09-20", 204, 400.0, 400.0, 0, 0, MAIN, "2024-09-20", None, None),
    (13, 14, "R/2", "2025-03-25", 204, 1000.0, 0.0, 0, 0, 0, "2025-04-15", None, None),
]
# VchCode, SrNo, RecType, MasterCode1, MasterCode2, Value1, Value3, D2, D4, D9, ShortNar, ClrDate
# Accounts: Value1 negative = Dr.  Items: Value1 positive = stock in; MasterCode2 = material centre.
LINES = [
    (1, 1, 1, 207, 0, -5000.0, 0, 0, 0, 0, None, None), (1, 2, 1, 210, 0, -900.0, 0, 0, 0, 0, None, None),
    (1, 3, 1, 205, 0, 5900.0, 0, 0, 0, 0, None, None),
    (1, 1, 2, 601, MAIN, 50.0, 5000.0, 0, 0, 0, None, None),
    (2, 1, 1, 204, 0, -11800.0, 0, 0, 0, 0, None, None), (2, 2, 1, 206, 0, 10000.0, 0, 0, 0, 0, None, None),
    (2, 3, 1, 209, 0, 1800.0, 0, 0, 0, 0, None, None),
    (2, 1, 2, 601, MAIN, -40.0, -10000.0, 295.0, 310.53, 5.0, None, None),
    (2, 1, 7, 601, MAIN, -40.0, -10000.0, 0, 0, 0, None, None),  # mirror line: must NOT be counted twice
    (3, 1, 1, 202, 0, -8000.0, 0, 0, 0, 0, "NEFT UTR123", "2024-06-05"),
    (3, 2, 1, 204, 0, 8000.0, 0, 0, 0, 0, None, None),
    (4, 1, 1, 208, 0, -3000.0, 0, 0, 0, 0, "June rent", None), (4, 2, 1, 203, 0, 3000.0, 0, 0, 0, 0, None, None),
    (5, 1, 1, 204, 0, 1180.0, 0, 0, 0, 0, None, None), (5, 2, 1, 206, 0, -1000.0, 0, 0, 0, 0, None, None),
    (5, 3, 1, 209, 0, -180.0, 0, 0, 0, 0, None, None),
    (5, 1, 2, 601, MAIN, 4.0, 1000.0, 0, 0, 0, None, None),
    (7, 1, 1, 205, 0, -5900.0, 0, 0, 0, 0, None, None),
    (7, 2, 1, 202, 0, 5900.0, 0, 0, 0, 0, "Chq 000123", None),  # not yet cleared by the bank
    (8, 1, 1, 204, 0, -99999.0, 0, 0, 0, 0, None, None), (8, 2, 1, 206, 0, 99999.0, 0, 0, 0, 0, None, None),
    (8, 1, 2, 601, MAIN, -999.0, -99999.0, 0, 0, 0, None, None),
    (9, 1, 1, 208, 0, -500.0, 0, 0, 0, 0, None, None), (9, 2, 1, 203, 0, 500.0, 0, 0, 0, 0, None, None),
    (10, 1, 2, 601, BRANCH, 5.0, 500.0, 0, 0, 0, None, None),
    (10, 1000, 2, 601, MAIN, -5.0, -500.0, 0, 0, 0, None, None),
    (11, 1, 20, 602, MAIN, 3.0, 360.0, 0, 0, 0, None, None),  # independent item line in RecType 20
    (12, 1, 2, 602, MAIN, -2.0, -400.0, 0, 0, 0, None, None),
]
# Tran3: RefCode, RecType, Method, VchCode, VchType, MasterCode1, MasterCode2, No, Date, DueDate,
#        Value1, Value3, NewRefAmount, MfgDate, Balance1, Balance2, Balance3
TRAN3 = [
    (5001, 1, 1, 2, 9, 204, 0, "S/1", "2024-05-05", "2024-06-04", -11800.0, 0, 0, None, 0, 0, 0),
    (5001, 1, 2, 3, 14, 204, 0, "S/1", "2024-06-01", None, 8000.0, 0, 0, None, 0, 0, 0),
    (5001, 1, 2, 5, 3, 204, 0, "S/1", "2024-07-01", None, 1180.0, 0, 0, None, 0, 0, 0),
    (5002, 1, 1, 1, 2, 205, 0, "P/1", "2024-04-10", "2024-05-10", 5900.0, 0, 0, None, 0, 0, 0),
    (5002, 1, 2, 7, 19, 205, 0, "P/1", "2025-03-20", None, -5900.0, 0, 0, None, 0, 0, 0),
    (7001, 4, 1, 6, 12, 602, 204, "SO/1", "2024-07-10", "2024-07-31", 5.0, 200.0, 1000.0, None, 0, 0, 0),
    (7001, 4, 2, 12, 11, 602, 204, "SO/1", "2024-09-20", None, -2.0, 200.0, 400.0, None, 0, 0, 0),
    (0, 2, 1, 1, 2, 601, 0, "B1", "2024-04-10", "2025-06-30", 50.0, 0, 0, "2024-01-01", 100.0, 250.0, 350.0),
]

EXPECTED = {
    "sharma_closing": "2,620.00 Dr",
    "total_income": 9000.0,
    "total_expenses": 8500.0,
    "net_profit": 500.0,
    "total_assets": 101220.0,
    "widget_a": (10, 59, 45, 24),
    "widget_b_closing": 1,
    "active_vouchers": 12,
    "ledger_count": 11,
}


def build(path: str) -> str:
    con = sqlite3.connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE Master1 (Code INTEGER PRIMARY KEY, MasterType INTEGER, Name TEXT, Alias TEXT, PrintName TEXT,
                              ParentGrp INTEGER, CM1 INTEGER DEFAULT 0, CM2 INTEGER DEFAULT 0, CM8 INTEGER DEFAULT 0,
                              D2 REAL DEFAULT 0, D3 REAL DEFAULT 0, D4 REAL DEFAULT 0, I2 INTEGER DEFAULT 0,
                              HSNCode TEXT, DeactiveMaster INTEGER DEFAULT 0, ModificationTime TEXT);
        CREATE TABLE Folio1 (MasterCode INTEGER, MasterType INTEGER, D1 REAL, D2 REAL, D3 REAL,
                             PRIMARY KEY (MasterCode, MasterType));
        CREATE TABLE MasterAddressInfo (MasterCode INTEGER PRIMARY KEY, Address1 TEXT, GSTNo TEXT, Mobile TEXT,
                                        WhatsAppNo TEXT, Email TEXT);
        CREATE TABLE Tran1 (VchCode INTEGER PRIMARY KEY, VchType INTEGER, VchNo TEXT, Date TEXT, VchSeriesCode INTEGER,
                            MasterCode1 INTEGER, MasterCode2 INTEGER, CM1 INTEGER, VchAmtBaseCur REAL,
                            VchSalePurcAmt REAL, StockUpdationDate TEXT, Cancelled INTEGER DEFAULT 0,
                            VchCancelled INTEGER DEFAULT 0, CreatedBy TEXT, CreationTime TEXT, ModifiedBy TEXT,
                            ModificationTime TEXT);
        CREATE TABLE Tran2 (VchCode INTEGER, SrNo INTEGER, RecType INTEGER, MasterCode1 INTEGER, MasterCode2 INTEGER,
                            Value1 REAL, Value2 REAL, Value3 REAL, D2 REAL, D4 REAL, D9 REAL, Date TEXT, VchNo TEXT,
                            VchType INTEGER, ShortNar TEXT, ClrDate TEXT);
        CREATE TABLE Tran3 (RefCode INTEGER, RecType INTEGER, Method INTEGER, VchCode INTEGER, VchType INTEGER,
                            MasterCode1 INTEGER, MasterCode2 INTEGER, No TEXT, Date TEXT, DueDate TEXT, Value1 REAL,
                            Value3 REAL, NewRefAmount REAL, MfgDate TEXT, Balance1 REAL, Balance2 REAL,
                            Balance3 REAL, ItemSrNo INTEGER DEFAULT 1);
        CREATE TABLE Tran4 (SrNo INTEGER, MasterCode1 INTEGER, MasterCode2 INTEGER, D1 REAL, D2 REAL, D3 REAL,
                            RecType INTEGER);
        CREATE TABLE VchOtherInfo (VchCode INTEGER PRIMARY KEY, Narration1 TEXT, Narration2 TEXT, VehicleNo TEXT);
        CREATE TABLE BillingDet (VchCode INTEGER PRIMARY KEY, PartyName TEXT, MobileNo TEXT, GSTNo TEXT);
        CREATE TABLE ItemParamDet (Date TEXT, VchType INTEGER, VchCode INTEGER, RecType INTEGER, ItemCode INTEGER,
                                   MCCode INTEGER, C1 TEXT, Value1 REAL);
        CREATE TABLE ItemSerialNo (VchCode INTEGER, VchType INTEGER, VchNo TEXT, Date TEXT, ItemCode INTEGER,
                                   MCCode INTEGER, SerialNo TEXT, Value1 REAL, WarrantyMonth INTEGER);
        CREATE TABLE TDS (Method INTEGER, VchCode INTEGER, VchType INTEGER, MasterCode1 INTEGER, MasterCode2 INTEGER,
                          EntryDate TEXT, VchAmt REAL, TDSRate REAL, TDSAmt REAL, ChallanNo TEXT, Quarter TEXT);
        CREATE TABLE BRSOpBal (SrNo INTEGER, MasterCode1 INTEGER, MasterCode2 INTEGER, OpAmt REAL, Type INTEGER,
                               ShortNar TEXT, EntryDate TEXT, ClrDate TEXT, VchNo TEXT);
        CREATE TABLE DeletedInfo (Type INTEGER, VchMastType INTEGER, Identity TEXT, DeletedBy TEXT, DeletionTime TEXT,
                                  OrgVchAmtBaseCur REAL, ComputerName TEXT);
        CREATE TABLE CheckList (Type INTEGER, Code INTEGER, Action INTEGER, ActionTime TEXT, UserName TEXT,
                                ComputerName TEXT);
        CREATE TABLE EventLog (UserName TEXT, Date TEXT, WorkedOn TEXT, Description TEXT, ComputerName TEXT);
        CREATE TABLE VchGSTSumItemWise (VchCode INTEGER, ItemCode INTEGER, VchDate TEXT, TaxRate REAL, TaxRate1 REAL);
        CREATE TABLE Config (RecType INTEGER, C1 TEXT, C2 TEXT);
        CREATE TABLE Locks (CompanyName TEXT, MainDbVer TEXT);
        """
    )
    for code, name, parent in GROUPS:
        c.execute("INSERT INTO Master1 (Code, MasterType, Name, ParentGrp) VALUES (?,1,?,?)", (code, name, parent))
    for code, name, parent, d1, credit in LEDGERS:
        c.execute("INSERT INTO Master1 (Code, MasterType, Name, ParentGrp, I2) VALUES (?,2,?,?,?)",
                  (code, name, parent, credit))
        c.execute("INSERT INTO Folio1 VALUES (?,2,?,?,0)", (code, d1, d1))
    for code, mt, name in OTHER_MASTERS:
        c.execute("INSERT INTO Master1 (Code, MasterType, Name, ParentGrp) VALUES (?,?,?,0)", (code, mt, name))
    for code, name, qty, val, mrp, sale, purc, hsn in ITEMS:
        c.execute("INSERT INTO Master1 (Code, MasterType, Name, Alias, ParentGrp, CM1, CM8, D2, D3, D4, HSNCode) "
                  "VALUES (?,6,?,?,501,?,?,?,?,?,?)", (code, name, f"W{name[-1]}", UNIT, TAX_CAT, mrp, sale, purc, hsn))
        c.execute("INSERT INTO Folio1 VALUES (?,6,?,?,?)", (code, qty, qty, val))
    c.execute("INSERT INTO MasterAddressInfo VALUES (204,'12 MG Road, Delhi','07ABCDE1234F1Z5','9999999999',"
              "'9999999999','sharma@example.com')")
    c.execute("INSERT INTO Locks VALUES ('Demo Traders Pvt Ltd', '21.0')")
    for (vc, typ, no, dt, party, total, goods, cancelled, cm1, mc, eff, mod_by, mod_at) in VOUCHERS:
        c.execute("INSERT INTO Tran1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,'admin','2024-01-01',?,?)",
                  (vc, typ, no, dt, SERIES, party or None, mc, cm1, total, goods, eff, cancelled, mod_by, mod_at))
    meta = {v[0]: (v[3], v[2], v[1]) for v in VOUCHERS}
    for vc, sr, rt, mc1, mc2, v1, v3, d2, d4, d9, nar, clr in LINES:
        dt, no, typ = meta[vc]
        c.execute("INSERT INTO Tran2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (vc, sr, rt, mc1, mc2, v1, v1, v3, d2, d4, d9, dt, no, typ, nar, clr))
    for row in TRAN3:
        c.execute("INSERT INTO Tran3 (RefCode, RecType, Method, VchCode, VchType, MasterCode1, MasterCode2, No, Date, "
                  "DueDate, Value1, Value3, NewRefAmount, MfgDate, Balance1, Balance2, Balance3) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    c.execute("INSERT INTO Tran4 VALUES (1, 601, ?, 10, 0, 0, 0)", (MAIN,))
    c.executemany("INSERT INTO VchOtherInfo VALUES (?,?,?,?)",
                  [(2, "Invoice for May order", None, "DL01AB1234"), (9, "Rent adjustment", None, None)])
    c.execute("INSERT INTO BillingDet VALUES (2, 'Sharma Traders', '9999999999', '07ABCDE1234F1Z5')")
    c.executemany("INSERT INTO ItemParamDet VALUES (?,?,?,?,?,?,?,?)", [
        ("2024-04-10", 2, 1, 2, 601, MAIN, "B1", 50.0), ("2024-05-05", 9, 2, 2, 601, MAIN, "B1", -40.0),
        ("2024-07-01", 3, 5, 2, 601, MAIN, "B1", 4.0)])
    c.executemany("INSERT INTO ItemSerialNo VALUES (?,?,?,?,?,?,?,?,?)", [
        (11, 8, "SJ/1", "2024-09-15", 602, MAIN, "SN-1", 1, 12),
        (11, 8, "SJ/1", "2024-09-15", 602, MAIN, "SN-2", 1, 12),
        (11, 8, "SJ/1", "2024-09-15", 602, MAIN, "SN-3", 1, 12),
        (12, 11, "SC/1", "2024-09-20", 602, MAIN, "SN-1", -1, 12),
        (12, 11, "SC/1", "2024-09-20", 602, MAIN, "SN-2", -1, 12)])
    c.execute("INSERT INTO TDS VALUES (1, 4, 19, 208, 211, '2024-06-15', 3000, 10, 300, NULL, 'Q1')")
    c.execute("INSERT INTO BRSOpBal VALUES (1, 202, 0, 1000, 1, 'Old cheque', '2024-03-25', NULL, 'CHQ-OLD')")
    c.execute("INSERT INTO DeletedInfo VALUES (1, 9, 'S/9', 'ravi', '2024-08-02 10:00:00', 5000, 'PC1')")
    c.execute("INSERT INTO CheckList VALUES (1, 2, 2, '2024-05-06 11:00:00', 'ravi', 'PC1')")
    c.execute("INSERT INTO EventLog VALUES ('ravi', '2024-05-06', 'Sales', 'Modified S/1', 'PC1')")
    c.execute("INSERT INTO VchGSTSumItemWise VALUES (2, 601, '2024-05-05', 9, 9)")
    c.execute("INSERT INTO Config VALUES (57, 'gstuser', 'super-secret-password')")
    con.commit()
    con.close()
    return path
