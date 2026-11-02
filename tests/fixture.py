"""Builds a tiny SQLite database with BUSY's table layout and sign conventions,
so every report can be tested without a Windows machine.

Expected figures (FY 2024-25) are written next to the data.
"""

from __future__ import annotations

import sqlite3

GROUPS = [  # Code, Name, ParentGrp
    (101, "Capital Account", 0), (102, "Current Assets", 0), (103, "Current Liabilities", 0),
    (104, "Revenue Accounts", 0), (110, "Sundry Debtors", 102), (111, "Bank Accounts", 102),
    (112, "Cash-in-hand", 102), (120, "Sundry Creditors", 103), (121, "Duties & Taxes", 103),
    (130, "Sale", 104), (131, "Purchase", 104), (132, "Expenses (Indirect/Admn.)", 104),
]
LEDGERS = [  # Code, Name, ParentGrp, Folio1.D1 opening (neg = Dr)
    (201, "Capital", 101, 100000.0), (202, "HDFC Bank", 111, -80000.0), (203, "Cash", 112, -20000.0),
    (204, "Sharma Traders", 110, 0.0), (205, "Gupta Suppliers", 120, 0.0), (206, "Sales", 130, 0.0),
    (207, "Purchase", 131, 0.0), (208, "Rent", 132, 0.0), (209, "Output GST", 121, 0.0),
    (210, "Input GST", 121, 0.0),
]
ITEM_GROUPS = [(501, "Electronics", 0)]
ITEMS = [(601, "Widget A", 501, 10.0, 1000.0), (602, "Widget B", 501, 0.0, 0.0)]

# VchCode, VchType, VchNo, Date, party, total, goods
VOUCHERS = [
    (1, 2, "P/1", "2024-04-10", 205, 5900.0, 5000.0),
    (2, 9, "S/1", "2024-05-05", 204, 11800.0, 10000.0),
    (3, 14, "R/1", "2024-06-01", 204, 8000.0, 0.0),
    (4, 19, "PY/1", "2024-06-15", 208, 3000.0, 0.0),
    (5, 3, "CN/1", "2024-07-01", 204, 1180.0, 1000.0),
    (6, 12, "SO/1", "2024-07-10", 204, 1180.0, 1000.0),
    (7, 19, "PY/2", "2025-03-20", 205, 5900.0, 0.0),
]
# VchCode, SrNo, RecType, MasterCode1, Value1, Value3, D2, D4, D9  (Value1 neg = Dr / item inward)
LINES = [
    (1, 1, 1, 207, -5000.0, 0, 0, 0, 0), (1, 2, 1, 210, -900.0, 0, 0, 0, 0), (1, 3, 1, 205, 5900.0, 0, 0, 0, 0),
    (1, 1, 2, 601, -50.0, -5000.0, 0, 0, 0),
    (2, 1, 1, 204, -11800.0, 0, 0, 0, 0), (2, 2, 1, 206, 10000.0, 0, 0, 0, 0), (2, 3, 1, 209, 1800.0, 0, 0, 0, 0),
    (2, 1, 2, 601, 40.0, 10000.0, 295.0, 310.53, 5.0),
    (3, 1, 1, 202, -8000.0, 0, 0, 0, 0), (3, 2, 1, 204, 8000.0, 0, 0, 0, 0),
    (4, 1, 1, 208, -3000.0, 0, 0, 0, 0), (4, 2, 1, 203, 3000.0, 0, 0, 0, 0),
    (5, 1, 1, 204, 1180.0, 0, 0, 0, 0), (5, 2, 1, 206, -1000.0, 0, 0, 0, 0), (5, 3, 1, 209, -180.0, 0, 0, 0, 0),
    (5, 1, 2, 601, -4.0, -1000.0, 0, 0, 0),
    (7, 1, 1, 205, -5900.0, 0, 0, 0, 0), (7, 2, 1, 202, 5900.0, 0, 0, 0, 0),
]


def build(path: str) -> str:
    con = sqlite3.connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE Master1 (Code INTEGER PRIMARY KEY, MasterType INTEGER, Name TEXT, Alias TEXT,
                              PrintName TEXT, ParentGrp INTEGER, D3 REAL DEFAULT 0);
        CREATE TABLE Folio1 (MasterCode INTEGER, MasterType INTEGER, D1 REAL, D2 REAL, D3 REAL);
        CREATE TABLE MasterAddressInfo (MasterCode INTEGER, Address1 TEXT, GSTNo TEXT, Mobile TEXT);
        CREATE TABLE Tran1 (VchCode INTEGER PRIMARY KEY, VchType INTEGER, VchNo TEXT, Date TEXT,
                            MasterCode1 INTEGER, VchAmtBaseCur REAL, VchSalePurcAmt REAL);
        CREATE TABLE Tran2 (VchCode INTEGER, SrNo INTEGER, RecType INTEGER, MasterCode1 INTEGER,
                            Value1 REAL, Value2 REAL, Value3 REAL, D2 REAL, D4 REAL, D9 REAL,
                            Date TEXT, VchNo TEXT);
        CREATE TABLE Tran3 (VchCode INTEGER, RecType INTEGER, ItemSrNo INTEGER, MasterCode1 INTEGER,
                            MasterCode2 INTEGER, Value1 REAL, Value2 REAL, Value3 REAL, NewRefAmount REAL);
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
        c.execute("INSERT INTO Master1 VALUES (?,6,?,?,NULL,?,300)", (code, name, name[-1] + "-ALIAS", parent))
        c.execute("INSERT INTO Folio1 VALUES (?,6,?,?,?)", (code, qty, qty, val))
    c.execute("INSERT INTO Master1 VALUES (801,8,'Pcs.',NULL,NULL,0,0)")  # a unit master
    c.execute("INSERT INTO MasterAddressInfo VALUES (204,'12 MG Road, Delhi','07ABCDE1234F1Z5','9999999999')")
    for v in VOUCHERS:
        c.execute("INSERT INTO Tran1 VALUES (?,?,?,?,?,?,?)", v)
    dates = {v[0]: (v[3], v[2]) for v in VOUCHERS}
    for vc, sr, rt, mc, v1, v3, d2, d4, d9 in LINES:
        c.execute("INSERT INTO Tran2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (vc, sr, rt, mc, v1, v1, v3, d2, d4, d9, dates[vc][0], dates[vc][1]))
    c.execute("INSERT INTO Tran3 VALUES (6,4,1,602,204,5,5,200,1000)")
    con.commit()
    con.close()
    return path
