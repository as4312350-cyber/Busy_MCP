"""Core accounting reports against the fixture company (hand-computed expectations)."""

from __future__ import annotations

import pytest

from busy_mcp import reports
from busy_mcp.config import settings_from
from busy_mcp.db import BusyDB, discover_targets
from busy_mcp.errors import ReportError
from fixtures.busy_fixture import EXPECTED


def test_overview_excludes_cancelled_and_reads_company_name(db):
    ov = reports.company_overview(db)
    assert ov["masters"]["Account"] == EXPECTED["ledger_count"]
    assert ov["masters"]["Material Centre"] == 2
    assert ov["vouchers"]["n"] == EXPECTED["active_vouchers"]
    assert ov["company"]["name"] == "Demo Traders Pvt Ltd"
    assert ov["target"]["financial_year"] == "2024-25"


def test_capabilities_detected(db):
    caps = reports.capabilities(db)
    assert caps["cancelled_voucher_columns"] == ["Cancelled", "VchCancelled"]
    assert caps["narration_source"] == "VchOtherInfo.Narration1"
    assert caps["item_inward_sign"] == 1 and "purchase" in caps["item_inward_sign_rule"]
    assert caps["item_line_rectypes"] == [2, 20]
    assert "RecType 7" in caps["item_line_rectypes_rule"] and "ignored" in caps["item_line_rectypes_rule"]
    assert all(caps["features_present"].values())


def test_voucher_types_labels_and_cancel_note(db):
    vt = reports.voucher_types(db)
    by = {v["vch_type"]: v for v in vt if "vch_type" in v}
    assert by[9]["label"] == "Sales" and by[9]["count"] == 1
    assert by[14]["label"] == "Receipt" and by[19]["label"] == "Payment"
    assert by[11]["label"] == "Sales Challan (inferred)"
    assert by[19]["sample_numbers"] == ["PY/2", "PY/1"]
    assert any("cancelled" in v.get("note", "") for v in vt)


def test_ledger_statement(db):
    st = reports.ledger_statement(db, "sharma traders", None, None)
    assert st["opening_balance"] == "0.00"
    assert [e["balance"] for e in st["entries"]] == ["11,800.00 Dr", "3,800.00 Dr", "2,620.00 Dr"]
    assert st["entries"][0]["particulars"].startswith("Sales")
    assert st["closing_balance"] == EXPECTED["sharma_closing"]


def test_ledger_statement_pagination_and_narration(db):
    st = reports.ledger_statement(db, "Sharma Traders", None, None, offset=0, limit=2)
    assert st["returned"] == 2 and st["has_more"] is True and st["next_offset"] == 2
    assert st["closing_balance"] == EXPECTED["sharma_closing"]
    bank = reports.ledger_statement(db, "HDFC Bank", None, None)
    assert bank["entries"][0]["narration"] == "NEFT UTR123"


def test_ledger_statement_mid_year_opening(db):
    st = reports.ledger_statement(db, "HDFC Bank", "2024-07-01", None)
    assert st["opening_balance"] == "88,000.00 Dr"
    assert st["closing_balance"] == "82,100.00 Dr"


def test_ledger_balance_and_details(db):
    assert reports.ledger_balance(db, "Cash", "2024-12-31")["closing_balance"] == "16,500.00 Dr"
    d = reports.ledger_details(db, "Sharma Traders")
    assert d["group_path"] == "Current Assets > Sundry Debtors"
    assert d["address_info"]["gstno"] == "07ABCDE1234F1Z5"


def test_trial_balance_tallies(db):
    tb = reports.trial_balance(db, None, None)
    assert tb["totals"]["net_difference"] == "0.00"
    assert tb["totals"]["closing_dr"] == tb["totals"]["closing_cr"]
    assert tb["totals"]["debit"] == tb["totals"]["credit"]
    assert "warning" not in tb


def test_trial_balance_group_only(db):
    tb = reports.trial_balance(db, None, None, ledger_level=False)
    assert "ledgers" not in tb
    assert {g["primary_group"] for g in tb["by_primary_group"]} >= {"Capital Account", "Revenue Accounts"}


def test_profit_and_loss(db):
    pl = reports.profit_and_loss(db, None, None)
    assert pl["total_income"] == EXPECTED["total_income"]
    assert pl["total_expenses"] == EXPECTED["total_expenses"]
    assert pl["net_profit_before_stock_adjustment"] == EXPECTED["net_profit"]
    assert {h["head"] for h in pl["expenses"]} == {"Purchase", "Expenses (Indirect/Admn.)"}


def test_profit_and_loss_heuristic_fallback(db_path):
    s = settings_from({"backend": "sqlite", "database": db_path, "fy": 2024, "pl_groups": ["Does Not Exist"]})
    with BusyDB(discover_targets(s)[0], s) as d:
        pl = reports.profit_and_loss(d, None, None)
    assert pl["net_profit_before_stock_adjustment"] == EXPECTED["net_profit"]
    assert pl["classification"].startswith("heuristic")


def test_balance_sheet_tallies(db):
    bs = reports.balance_sheet(db, None)
    assert bs["difference"] == 0.0
    assert bs["total_assets"] == EXPECTED["total_assets"]


def test_outstanding_with_aging(db):
    rec = reports.outstanding(db, "receivable", "2025-03-31")
    assert rec["total_outstanding"] == 2620.0
    assert rec["parties"][0]["party"] == "Sharma Traders"
    assert rec["parties"][0]["aging"] == {"180+": 2620.0}
    assert reports.outstanding(db, "payable", "2025-03-31")["total"] == 0
    assert reports.outstanding(db, "payable", "2024-12-31")["total_outstanding"] == 5900.0


def test_stock_summary_handles_rectypes_transfers_and_cancellations(db):
    ss = reports.stock_summary(db, None, None)
    by = {i["item"]: i for i in ss["items"]}
    a = by["Widget A"]
    assert (a["opening_qty"], a["inward_qty"], a["outward_qty"], a["closing_qty"]) == EXPECTED["widget_a"]
    assert by["Widget B"]["closing_qty"] == EXPECTED["widget_b_closing"]
    assert reports.stock_summary(db, None, None, only_negative=True)["total"] == 0


def test_stock_item_movement(db):
    mv = reports.stock_item_movement(db, "Widget A", "2024-05-01", None)
    assert mv["opening_qty"] == 60
    assert mv["closing_qty"] == 24
    assert {e.get("material_centre") for e in mv["entries"]} == {"Main Store", "Branch Godown"}


def test_configured_conventions_override_detection(db_path):
    s = settings_from({"backend": "sqlite", "database": db_path, "fy": 2024,
                       "item_inward_sign": -1, "item_rectypes": "2"})
    with BusyDB(discover_targets(s)[0], s) as d:
        assert reports.item_inward_sign(d)[1].startswith("configured")
        assert reports.item_rectypes(d) == ([2], "configured (BUSY_ITEM_RECTYPES)")


def test_voucher_details_end_to_end(db):
    v = reports.voucher_details(db, vch_no="S/1")
    assert v["party"] == "Sharma Traders" and v["balanced"] is True
    assert v["narration"] == "Invoice for May order"
    assert (v["series"], v["sale_purchase_type"], v["material_centre"]) == ("Main", "L/GST-18%", "Main Store")
    assert len(v["item_entries"]) == 1  # the RecType 7 mirror is not shown as a second item
    assert v["item_entries"][0]["discount_pct"] == 5.0 and v["item_entries"][0]["material_centre"] == "Main Store"
    assert v["bill_references"][0]["method"] == "new bill"
    assert v["other_info"]["vehicleno"] == "DL01AB1234"
    assert v["billing_details"]["gstno"] == "07ABCDE1234F1Z5"
    assert v["gst_lines"][0]["taxrate"] == 9
    assert v["batches"][0]["c1"] == "B1"
    so = reports.voucher_details(db, vch_no="SO/1")
    assert so["order_lines"][0] == {"item": "Widget B", "qty": 5.0, "rate": 200.0, "amount": 1000.0}
    assert reports.voucher_details(db, vch_code=8)["cancelled"] is True
    assert reports.voucher_details(db, vch_no="PY/1")["tds"][0]["tdsamt"] == 300


def test_day_book(db):
    assert reports.day_book(db, "2024-06-01", "2024-06-30")["total"] == 2
    assert reports.day_book(db, None, None, vch_type=19)["total"] == 2
    je = reports.day_book(db, None, None, vch_type=16)["vouchers"][0]
    assert je["amount"] == 500.0 and je["narration"] == "Rent adjustment"
    assert reports.day_book(db, None, None, vch_type=9)["total"] == 1


def test_trade_analysis(db):
    by_party = reports.trade_analysis(db, "sales", "party", None, None)
    assert by_party["lines"][0] == {"party": "Sharma Traders", "invoices": 1, "gross_incl_tax": 11800.0,
                                    "returns_incl_tax": 1180.0, "net_incl_tax": 10620.0, "net_taxable_value": 9000.0}
    assert reports.trade_analysis(db, "sales", "item", None, None)["lines"][0]["net_qty"] == 36.0
    by_month = reports.trade_analysis(db, "purchase", "month", None, None)
    assert by_month["lines"][0]["month"] == "2024-04" and by_month["totals"]["net_incl_tax"] == 5900.0


def test_chart_search_and_list(db):
    coa = reports.chart_of_accounts(db, include_ledgers=True)
    assert [n["name"] for n in coa] == ["Capital Account", "Current Assets", "Current Liabilities", "Revenue Accounts"]
    assert {h["name"] for h in reports.search_masters(db, "widget")["matches"]} == {"Widget A", "Widget B"}
    assert reports.list_masters(db, "ledger", under_group="Current Assets")["total"] == 3
    page = reports.list_masters(db, "ledger", offset=8, limit=5)
    assert page["returned"] == 3 and page["has_more"] is False


def test_group_summary(db):
    assert reports.group_summary(db, "Current Assets", None, None)["total_closing"] == "101,220.00 Dr"


@pytest.mark.parametrize("call", [
    lambda d: reports.ledger_balance(d, "nonexistent", None),
    lambda d: reports.ledger_balance(d, "GST", None),
    lambda d: reports.ledger_statement(d, "Cash", "2024-13-01", None),
    lambda d: reports.ledger_statement(d, "Cash", "2025-01-01", "2024-01-01"),
    lambda d: reports.search_masters(d, "  "),
    lambda d: reports.voucher_details(d),
    lambda d: reports.outstanding(d, "sideways", None),
])
def test_bad_input_raises_report_error(db, call):
    with pytest.raises(ReportError):
        call(db)
