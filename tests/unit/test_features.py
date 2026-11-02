"""Feature reports beyond core accounting: bills, orders, banking, taxes, inventory
detail, audit, analytics and the generic explorer."""

from __future__ import annotations

import pytest

from busy_mcp import reports
from busy_mcp.config import settings_from
from busy_mcp.db import BusyDB, discover_targets
from busy_mcp.errors import ReportError


def test_bills_outstanding_matches_ledger(db):
    out = reports.bills_outstanding(db, "receivable", as_of="2025-03-31")
    p = out["parties"][0]
    assert (p["party"], p["pending_bills_total"], p["ledger_balance"], p["on_account_or_unallocated"]) == (
        "Sharma Traders", 2620.0, 2620.0, 0.0)
    bill = p["bills"][0]
    assert (bill["bill_no"], bill["due_date"], bill["pending"], bill["overdue_days"]) == (
        "S/1", "2024-06-04", 2620.0, 300)
    assert p["overdue_aging"] == {"180+": 2620.0} and p["credit_days"] == 30
    assert "RefCode" in out["method"]


def test_bills_outstanding_payable_respects_as_of(db):
    assert reports.bills_outstanding(db, "payable", as_of="2024-12-31")["total_pending"] == 5900.0
    assert reports.bills_outstanding(db, "payable", as_of="2025-03-31")["total_pending"] == 0.0
    single = reports.bills_outstanding(db, "payable", party="Gupta Suppliers", as_of="2024-12-31")
    assert single["parties"][0]["bills"][0]["bill_no"] == "P/1"


def test_pending_orders(db):
    out = reports.pending_orders(db, "sales")
    assert out["orders"] == [{"order_no": "SO/1", "date": "2024-07-10", "due_date": "2024-07-31",
                              "party": "Sharma Traders", "item": "Widget B", "ordered_qty": 5.0,
                              "executed_qty": 2.0, "pending_qty": 3.0, "rate": 200.0, "pending_value": 600.0}]
    assert reports.pending_orders(db, "purchase")["total"] == 0


def test_bank_reconciliation(db):
    brs = reports.bank_reconciliation(db, "HDFC Bank")
    assert brs["balance_as_per_books"] == "82,100.00 Dr"
    assert brs["cheques_issued_not_yet_presented"] == 6900.0
    assert brs["balance_as_per_bank"] == "89,000.00 Dr"
    nos = {e["vch_no"] for e in brs["uncleared_entries"]}
    assert nos == {"PY/2", "CHQ-OLD"}
    py2 = next(e for e in brs["uncleared_entries"] if e["vch_no"] == "PY/2")
    assert py2["party"] == "Gupta Suppliers"
    early = reports.bank_reconciliation(db, "HDFC Bank", as_of="2024-06-03")
    assert early["deposits_not_yet_credited_by_bank"] == 8000.0  # R/1 cleared only on 05-Jun


def test_post_dated_cheques(db):
    out = reports.post_dated_cheques(db, as_of="2025-03-31")
    assert out["cheques"][0]["vch_no"] == "R/2" and out["pdc_receipts_total"] == 1000.0
    assert reports.post_dated_cheques(db, as_of="2025-04-30")["total"] == 0


def test_gst_summary(db):
    g = reports.gst_summary(db, None, None, monthly=True)
    assert g["tax_by_head"] == {"GST": {"output": 1620.0, "input": 900.0, "net_payable": 720.0}}
    assert g["turnover_by_tax_type"]["sales"] == [
        {"tax_type": "L/GST-18%", "vouchers": 2, "taxable_value": 9000.0, "invoice_value": 10620.0}]
    out_ledger = next(x for x in g["tax_ledgers"] if x["ledger"] == "Output GST")
    assert out_ledger["monthly_net"]["2024-05"] == 1800.0


def test_tds_register(db):
    t = reports.tds_register(db, None, None)
    assert t["total_tds"] == 300.0 and t["pending_challan"] == 1
    assert t["entries"][0]["party"] == "Rent" and t["entries"][0]["tds_account"] == "TDS Payable"


def test_stock_by_location(db):
    loc = reports.stock_by_location(db, None)
    assert loc["totals_by_location"] == {"Branch Godown": 5.0, "Main Store": 20.0}
    only_branch = reports.stock_by_location(db, None, location="Branch Godown")
    assert only_branch["stock"] == [{"item": "Widget A", "location": "Branch Godown", "closing_qty": 5.0}]


def test_batch_stock_with_expiry(db):
    b = reports.batch_stock(db, expiring_within_days=120)
    assert b["batches"] == [{"item": "Widget A", "batch": "B1", "qty": 14.0, "expiry": "2025-06-30",
                             "mfg_date": "2024-01-01", "mrp": 350.0, "purchase_rate": 100.0, "sale_rate": 250.0,
                             "days_to_expiry": 91}]
    assert reports.batch_stock(db, expiring_within_days=30)["total"] == 0


def test_serial_numbers(db):
    s = reports.serial_numbers(db)
    assert [x["serial_no"] for x in s["serials"]] == ["SN-3"]
    assert reports.serial_numbers(db, in_stock_only=False)["total"] == 3
    assert reports.serial_numbers(db, serial="SN-1", in_stock_only=False)["serials"][0]["last_voucher"] == "SC/1"


def test_audit_trail(db):
    a = reports.audit_trail(db, None, None)
    assert a["vouchers_modified"][0]["vch_no"] == "S/1" and a["vouchers_modified"][0]["modified_by"] == "ravi"
    assert a["deleted"][0]["identity"] == "S/9"
    assert a["activity_by_user"] == {"ravi": 1, "ravi (deletions)": 1}
    assert a["user_activity"][0]["description"] == "Modified S/1"
    assert reports.audit_trail(db, None, None, user="nobody")["activity_by_user"] == {}


def test_analytics(db):
    d = reports.dashboard(db)
    assert (d["sales"]["net_incl_tax"], d["purchases"]["net_incl_tax"], d["net_profit_before_stock_adjustment"]) == (
        10620.0, 5900.0, 500.0)
    assert (d["receivables"], d["payables"], d["cash_and_bank"]) == (2620.0, 0.0, "98,600.00 Dr")
    m = reports.ledger_monthly(db, "Sale")
    assert m["kind"] == "group" and m["closing"] == "9,000.00 Cr" and len(m["months"]) == 12
    cb = reports.cash_bank_summary(db)
    assert cb["total_closing"] == "98,600.00 Dr" and cb["monthly_flow"]["2024-06"]["in"] == 8000.0


def test_catalog_and_blocked_tables(db):
    cat = reports.catalog(db, with_row_counts=True)
    system = {t["table"]: t for t in cat["categories"]["System"]}
    assert system["Config"]["blocked"] is True
    assert {t["table"] for t in cat["categories"]["Vouchers"]} >= {"Tran1", "Tran2", "Tran3"}
    tran1 = next(t for t in cat["categories"]["Vouchers"] if t["table"] == "Tran1")
    assert tran1["rows"] == 13
    with pytest.raises(ReportError, match="blocked"):
        reports.read_table(db, "Config")


def test_blocked_tables_can_be_allowed_explicitly(db_path):
    s = settings_from({"backend": "sqlite", "database": db_path, "fy": 2024, "allow_sensitive": True})
    with BusyDB(discover_targets(s)[0], s) as d:
        rows = reports.read_table(d, "Config")["rows"]
    assert rows[0]["c1"] == "gstuser"


def test_read_table_filters_decoding_and_paging(db):
    out = reports.read_table(db, "tran2", columns=["VchCode", "MasterCode1", "Value1"],
                             filters=[{"column": "mastercode1", "op": "eq", "value": "Sharma Traders"},
                                      {"column": "Value1", "op": "lt", "value": 0}], order_by="VchCode")
    assert [r["vchcode"] for r in out["rows"]] == [2, 8]
    assert out["rows"][0]["mastercode1_name"] == "Sharma Traders (Account)"
    page = reports.read_table(db, "Tran1", limit=5, offset=5)
    assert page["returned"] == 5 and page["has_more"] is True and page["next_offset"] == 10
    dated = reports.read_table(db, "Tran1", date_from="2024-06-01", date_to="2024-06-30")
    assert [r["vchno"] for r in dated["rows"]] == ["R/1", "PY/1"]
    contains = reports.read_table(db, "Tran1", filters=[{"column": "VchNo", "op": "contains", "value": "PY/"}])
    assert len(contains["rows"]) == 2
    inn = reports.read_table(db, "Tran1", filters=[{"column": "VchType", "op": "in", "value": [12, 13]}])
    assert [r["vchno"] for r in inn["rows"]] == ["SO/1"]


@pytest.mark.parametrize("kwargs", [
    {"table": "Nope"},
    {"table": "Tran1", "columns": ["NoSuchColumn"]},
    {"table": "Tran1", "filters": [{"column": "VchNo", "op": "regex", "value": "x"}]},
    {"table": "Tran1", "filters": [{"column": "VchNo", "op": "contains", "value": "%"}]},
    {"table": "Tran1", "offset": 30000},
    {"table": "Locks", "date_from": "2024-01-01"},
])
def test_read_table_rejects_bad_input(db, kwargs):
    with pytest.raises(ReportError):
        reports.read_table(db, **kwargs)


def test_find_everywhere(db):
    by_phone = reports.find(db, "9999999999")
    assert set(by_phone["sources_with_hits"]) == {"party_contacts", "billing_details"}
    assert by_phone["results"]["party_contacts"][0]["mastercode_name"] == "Sharma Traders (Account)"
    assert "voucher_numbers" in reports.find(db, "SO/1")["sources_with_hits"]
    assert "bill_and_batch_refs" in reports.find(db, "B1")["sources_with_hits"]
    assert "serial_numbers" in reports.find(db, "SN-3")["sources_with_hits"]
    assert reports.find(db, "widget")["results"]["masters"][0]["type"] == "Item"
    assert "line_narrations" in reports.find(db, "UTR123", deep=True)["sources_with_hits"]
    with pytest.raises(ReportError):
        reports.find(db, "x")


def test_master_details_for_any_type(db):
    item = reports.master_details(db, "Widget A")
    known = item["known_fields"]
    assert known["main unit"]["value"] == "Pcs. (Unit)"
    assert known["tax category"]["value"] == "GST 18% (Tax Category)"
    assert known["MRP"]["value"] == 350.0 and known["sale price"]["value"] == 300.0
    assert known["HSN/SAC code"]["value"] == "8471"
    assert item["stock"]["closing_qty_at_fy_end"] == 24
    party = reports.master_details(db, "Sharma Traders")
    assert party["balance_at_fy_end"] == "2,620.00 Dr"
    assert party["known_fields"]["credit days"]["value"] == 30
    assert party["address_info"]["mobile"] == "9999999999"
    godown = reports.master_details(db, "Main Store")
    assert godown["type"] == "Material Centre"
    group = reports.master_details(db, "Current Assets")
    assert group["children"]["count"] == 3


def test_sensitive_columns_are_masked_and_not_filterable(writable_db_path):
    import sqlite3

    con = sqlite3.connect(writable_db_path)
    con.execute("CREATE TABLE UserInfo (UserName TEXT, UserPassword TEXT)")
    con.execute("INSERT INTO UserInfo VALUES ('ravi', 'hunter2')")
    con.commit()
    con.close()
    s = settings_from({"backend": "sqlite", "database": writable_db_path, "fy": 2024})
    with BusyDB(discover_targets(s)[0], s) as d:
        rows = reports.read_table(d, "UserInfo", compact=False)["rows"]
        assert rows == [{"username": "ravi"}]  # credential column not even selected
        explicit = reports.read_table(d, "UserInfo", columns=["UserName", "UserPassword"])["rows"]
        assert explicit == [{"username": "ravi"}]
        with pytest.raises(ReportError, match="never returned"):
            reports.read_table(d, "UserInfo", columns=["UserPassword"])
        with pytest.raises(ReportError, match="cannot be filtered"):
            reports.read_table(d, "UserInfo", filters=[{"column": "UserPassword", "op": "contains", "value": "hu"}])
        with pytest.raises(ReportError, match="cannot be filtered"):
            reports.read_table(d, "UserInfo", order_by="UserPassword")


def test_gst_ignores_sales_ledgers_named_like_taxes(writable_db_path):
    import sqlite3

    con = sqlite3.connect(writable_db_path)
    con.execute("UPDATE Master1 SET Name = 'Sales GST 18%' WHERE Code = 206")  # revenue ledger with GST in its name
    con.commit()
    con.close()
    s = settings_from({"backend": "sqlite", "database": writable_db_path, "fy": 2024})
    with BusyDB(discover_targets(s)[0], s) as d:
        g = reports.gst_summary(d, None, None)
        assert {x["ledger"] for x in g["tax_ledgers"]} == {"Output GST", "Input GST"}
        assert g["net_gst_payable"] == 720.0 and "Duties & Taxes" in g["method"]
    s2 = settings_from({"backend": "sqlite", "database": writable_db_path, "fy": 2024, "tax_groups": ["Nope"]})
    with BusyDB(discover_targets(s2)[0], s2) as d:
        g = reports.gst_summary(d, None, None)
        assert {x["ledger"] for x in g["tax_ledgers"]} == {"Output GST", "Input GST"}
        assert "outside the P&L groups" in g["method"]


def test_stock_by_location_keeps_opening_without_centre_data(writable_db_path):
    import sqlite3

    con = sqlite3.connect(writable_db_path)
    con.execute("DELETE FROM Tran4")
    con.commit()
    con.close()
    s = settings_from({"backend": "sqlite", "database": writable_db_path, "fy": 2024})
    with BusyDB(discover_targets(s)[0], s) as d:
        loc = reports.stock_by_location(d, None)
        assert loc["totals_by_location"] == {"(not allocated)": 10.0, "Branch Godown": 5.0, "Main Store": 10.0}
        assert sum(loc["totals_by_location"].values()) == 25.0  # = summary closing (24 + 1)
        assert "not allocated" in loc["method"]
