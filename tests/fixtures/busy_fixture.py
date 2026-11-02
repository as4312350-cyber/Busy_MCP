"""Builds a small SQLite database with BUSY's table layout, column names and sign
conventions (taken from a real BUSY schema dump), so every report can be tested
without Windows or a BUSY licence.

Expected figures for FY 2024-25 are documented next to the data and asserted in
tests/unit/test_reports.py.
"""

from __future__ import annotations

import sqlite3

GROUPS = [  # Code, Name, ParentGrp  (BUSY reserves codes 101-129 for built-in groups)
    (101, "Capital Account", 0), (102, "Current Assets", 0), (103, "Current Liabilities", 0),
    (104, "Revenue Accounts", 0), (110, "Sundry Debtors", 102), (111, "Bank Accounts", 102),
    (112, "Cash-in-hand", 102), (120, "Sundry Creditors", 103), (121, "Duties & Taxes", 103),
    (130, "Sale", 104), (131, "Purchase", 104), (132, "Expenses (Indirect/Admn.)", 104),
]
LEDGERS = [  # Code, Name, ParentGrp, Folio1.D1 opening (negative = Dr)
    (201, "Capital", 101, 100000.0), (202, "HDFC Bank", 111, -80000.0), (203, "Cash", 112, -20000.0),
    (204, "Sharma Traders", 110, 0.0), (205, "Gupta Suppliers", 120, 0.0), (206, "Sales", 130, 0.0),
    (207, "Purchase", 131, 0.0), (208, "Rent", 132, 0.0), (209, "Output GST", 121, 0.0),
    (210, "Input GST", 121, 0.0),
]
ITEM_GROUPS = [(501, "Electronics", 0)]
ITEMS = [(601, "Widget A", 501, 10.0, 1000.0), (602, "Widget B", 501, 0.0, 0.0)]
SERIES = [(901, "Main", 25)]

# VchCode, VchType, VchNo, Date, party, total, goods, cancelled
VOUCHERS = [
    (1, 2, "P/1", "2024-04-10", 205, 5900.0, 5000.0, 0),
    (2, 9, "S/1", "2024-05-05", 204, 11800.0, 10000.0, 0),
    (3, 14, "R/1", "2024-06-01", 204, 8000.0, 0.0, 0),
    (4, 19, "PY/1", "2024-06-15", 208, 3000.0, 0.0, 0),
    (5, 3, "CN/1", "2024-07-01", 204, 1180.0, 1000.0, 0),
    (6, 12, "SO/1", "2024-07-10", 204, 1180.0, 1000.0, 0),
    (7, 19, "PY/2", "2025-03-20", 205, 5900.0, 0.0, 0),
    (8, 9, "S/2", "2024-08-01", 204, 99999.0, 99999.0, 1),  # CANCELLED: must never be counted
    (9, 16, "JE/1", "2024-09-01", 0, 0.0, 0.0, 0),  # journal with empty header total
]
# VchCode, SrNo, RecType, MasterCode1, Value1, Value3, D2, D4, D9, ShortNar
# Accounts: Value1 negative = Dr.  Items: Value1 positive = stock IN.
LINES = [
    (1, 1, 1, 207, -5000.0, 0, 0, 0, 0, None), (1, 2, 1, 210, -900.0, 0, 0, 0, 0, None),
    (1, 3, 1, 205, 5900.0, 0, 0, 0, 0, None),
    (1, 1, 2, 601, 50.0, 5000.0, 0, 0, 0, None),
    (2, 1, 1, 204, -11800.0, 0, 0, 0, 0, None), (2, 2, 1, 206, 10000.0, 0, 0, 0, 0, None),
    (2, 3, 1, 209, 1800.0, 0, 0, 0, 0, None),
    (2, 1, 2, 601, -40.0, -10000.0, 295.0, 310.53, 5.0, None),
    (3, 1, 1, 202, -8000.0, 0, 0, 0, 0, "NEFT UTR123"), (3, 2, 1, 204, 8000.0, 0, 0, 0, 0, None),
    (4, 1, 1, 208, -3000.0, 0, 0, 0, 0, "June rent"), (4, 2, 1, 203, 3000.0, 0, 0, 0, 0, None),
    (5, 1, 1, 204, 1180.0, 0, 0, 0, 0, None), (5, 2, 1, 206, -1000.0, 0, 0, 0, 0, None),
    (5, 3, 1, 209, -180.0, 0, 0, 0, 0, None),
    (5, 1, 2, 601, 4.0, 1000.0, 0, 0, 0, None),
    (7, 1, 1, 205, -5900.0, 0, 0, 0, 0, None), (7, 2, 1, 202, 5900.0, 0, 0, 0, 0, None),
    (8, 1, 1, 204, -99999.0, 0, 0, 0, 0, None), (8, 2, 1, 206, 99999.0, 0, 0, 0, 0, None),
    (8, 1, 2, 601, -999.0, -99999.0, 0, 0, 0, None),
    (9, 1, 1, 208, -500.0, 0, 0, 0, 0, None), (9, 2, 1, 203, 500.0, 0, 0, 0, 0, None),
]

EXPECTED = {
    "sharma_closing": "2,620.00 Dr",
    "total_income": 9000.0,
    "total_expenses": 8500.0,
    "net_profit": 500.0,
    "total_assets": 101220.0,
    "widget_a": (10, 54, 40, 24),
}


def build(path: str) -> str:
    con = sqlite3.connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE Master1 (Code INTEGER PRIMARY KEY, MasterType INTEGER, Name TEXT, Alias TEXT,
                              PrintName TEXT, ParentGrp INTEGER, D3 REAL DEFAULT 0);
        CREATE TABLE Folio1 (MasterCode INTEGER, MasterType INTEGER, D1 REAL, D2 REAL, D3 REAL,
                             PRIMARY KEY (MasterCode, MasterType));
        CREATE TABLE MasterAddressInfo (MasterCode INTEGER PRIMARY KEY, Address1 TEXT, GSTNo TEXT,
                                        Mobile TEXT, Email TEXT);
        CREATE TABLE Tran1 (VchCode INTEGER PRIMARY KEY, VchType INTEGER, VchNo TEXT, Date TEXT,
                            VchSeriesCode INTEGER, MasterCode1 INTEGER, VchAmtBaseCur REAL,
                            VchSalePurcAmt REAL, Cancelled INTEGER DEFAULT 0, VchCancelled INTEGER DEFAULT 0,
                            CreatedBy TEXT, CreationTime TEXT);
        CREATE TABLE Tran2 (VchCode INTEGER, SrNo INTEGER, RecType INTEGER, MasterCode1 INTEGER,
                            Value1 REAL, Value2 REAL, Value3 REAL, D2 REAL, D4 REAL, D9 REAL,
                            Date TEXT, VchNo TEXT, VchType INTEGER, ShortNar TEXT);
        CREATE TABLE Tran3 (VchCode INTEGER, RecType INTEGER, ItemSrNo INTEGER, MasterCode1 INTEGER,
                            MasterCode2 INTEGER, Value1 REAL, Value2 REAL, Value3 REAL, NewRefAmount REAL);
        CREATE TABLE VchOtherInfo (VchCode INTEGER PRIMARY KEY, Narration1 TEXT, Narration2 TEXT);
        CREATE TABLE Locks (CompanyName TEXT, MainDbVer TEXT);
        """
    )
    for code, name, parent in GROUPS:
        c.execute("INSERT INTO Master1 VALUES (?,1,?,NULL,NULL,?,0)", (code, name, parent))
    for code, name, parent, d1 in LEDGERS:
        c.execute("INSERT INTO Master1 VALUES (?,2,?,NULL,NULL,?,0)", (code, name, parent))
        c.execute("INSERT INTO Folio1 VALUES (?,2,?,?,0)", (code, d1, d1))
    for code, name, parent in ITEM_GROUPS:
        c.execute("INSERT INTO Master1 VALUES (?,5,?,NULL,NULL,?,0)", (code, name, parent))
    for code, name, parent, qty, val in ITEMS:
        c.execute("INSERT INTO Master1 VALUES (?,6,?,?,NULL,?,300)", (code, name, f"W{name[-1]}", parent))
        c.execute("INSERT INTO Folio1 VALUES (?,6,?,?,?)", (code, qty, qty, val))
    for code, name, mt in SERIES:
        c.execute("INSERT INTO Master1 VALUES (?,?,?,NULL,NULL,0,0)", (code, mt, name))
    c.execute("INSERT INTO Master1 VALUES (801,8,'Pcs.',NULL,NULL,0,0)")
    c.execute("INSERT INTO MasterAddressInfo VALUES (204,'12 MG Road, Delhi','07ABCDE1234F1Z5','9999999999',NULL)")
    c.execute("INSERT INTO Locks VALUES ('Demo Traders Pvt Ltd', '21.0')")
    for vc, typ, no, dt, party, total, goods, cancelled in VOUCHERS:
        c.execute("INSERT INTO Tran1 VALUES (?,?,?,?,901,?,?,?,?,0,'admin','2024-01-01')",
                  (vc, typ, no, dt, party or None, total, goods, cancelled))
    meta = {v[0]: (v[3], v[2], v[1]) for v in VOUCHERS}
    for vc, sr, rt, mc, v1, v3, d2, d4, d9, nar in LINES:
        dt, no, typ = meta[vc]
        c.execute("INSERT INTO Tran2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (vc, sr, rt, mc, v1, v1, v3, d2, d4, d9, dt, no, typ, nar))
    c.execute("INSERT INTO Tran3 VALUES (6,4,1,602,204,5,5,200,1000)")
    c.execute("INSERT INTO VchOtherInfo VALUES (2,'Invoice for May order',NULL)")
    c.execute("INSERT INTO VchOtherInfo VALUES (9,'Rent adjustment',NULL)")
    con.commit()
    con.close()
    return path
