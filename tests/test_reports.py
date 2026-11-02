import pytest

from busy_mcp import reports
from busy_mcp.config import Settings
from busy_mcp.db import BusyDB, discover_targets

from .fixture import build


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = build(str(tmp_path_factory.mktemp("busy") / "db12024.sqlite"))
    s = Settings(backend="sqlite", database=path, fy=2024)
    return BusyDB(discover_targets(s)[0], s)


def test_overview(db):
    ov = reports.company_overview(db)
    assert ov["masters"]["Account (Ledger)"] == 10
    assert ov["vouchers"]["n"] == 7
    assert ov["target"]["financial_year"] == "2024-25"


def test_voucher_types(db):
    vt = {v["vch_type"]: v for v in reports.voucher_types(db)}
    assert vt[9]["label"] == "Sales"
    assert vt[14]["label"] == "Receipt (inferred)"
    assert vt[19]["count"] == 2


def test_ledger_statement_running_balance(db):
    st = reports.ledger_statement(db, "sharma traders", None, None)
    assert st["opening_balance"] == "0.00"
    assert [e["balance"] for e in st["entries"]] == ["11,800.00 Dr", "3,800.00 Dr", "2,620.00 Dr"]
    assert st["entries"][0]["particulars"].startswith("Sales")
    assert st["closing_balance"] == "2,620.00 Dr"


def test_ledger_statement_mid_year_opening(db):
    st = reports.ledger_statement(db, "HDFC Bank", "2024-07-01", None)
    assert st["opening_balance"] == "88,000.00 Dr"
    assert st["closing_balance"] == "82,100.00 Dr"


def test_ledger_balance_and_details(db):
    assert reports.ledger_balance(db, "Cash", "2024-12-31")["closing_balance"] == "17,000.00 Dr"
    d = reports.ledger_details(db, "Sharma Traders")
    assert d["group_path"] == "Current Assets > Sundry Debtors"
    assert d["address_info"]["gstno"] == "07ABCDE1234F1Z5"


def test_trial_balance_balances(db):
    tb = reports.trial_balance(db, None, None)
    assert tb["totals"]["net_difference"] == "0.00"
    assert tb["totals"]["closing_dr"] == tb["totals"]["closing_cr"]
    assert tb["totals"]["debit"] == tb["totals"]["credit"]


def test_profit_and_loss(db):
    pl = reports.profit_and_loss(db, None, None)
    assert pl["total_income"] == 9000.0
    assert pl["total_expenses"] == 8000.0
    assert pl["net_profit_before_stock_adjustment"] == 1000.0
    heads = {h["head"] for h in pl["expenses"]}
    assert heads == {"Purchase", "Expenses (Indirect/Admn.)"}


def test_balance_sheet_tallies(db):
    bs = reports.balance_sheet(db, None)
    assert bs["difference"] == 0.0
    assert bs["total_assets"] == 101720.0


def test_outstanding_with_aging(db):
    rec = reports.outstanding(db, "receivable", "2025-03-31")
    assert rec["total_outstanding"] == 2620.0
    p = rec["parties"][0]
    assert p["party"] == "Sharma Traders"
    assert p["aging"] == {"180+": 2620.0}
    pay = reports.outstanding(db, "payable", "2025-03-31")
    assert pay["party_count"] == 0
    pay_mid = reports.outstanding(db, "payable", "2024-12-31")
    assert pay_mid["total_outstanding"] == 5900.0


def test_stock_summary(db):
    ss = reports.stock_summary(db, None, None)
    a = next(i for i in ss["items"] if i["item"] == "Widget A")
    assert (a["opening_qty"], a["inward_qty"], a["outward_qty"], a["closing_qty"]) == (10, 54, 40, 24)
    assert "purchase" in ss["notes"][0]


def test_stock_item_movement(db):
    mv = reports.stock_item_movement(db, "Widget A", "2024-05-01", None)
    assert mv["opening_qty"] == 60
    assert mv["closing_qty"] == 24


def test_voucher_details(db):
    v = reports.voucher_details(db, vch_no="S/1")
    assert v["party"] == "Sharma Traders"
    assert sum(e["debit"] for e in v["ledger_entries"]) == sum(e["credit"] for e in v["ledger_entries"])
    assert v["item_entries"][0]["discount_pct"] == 5.0
    so = reports.voucher_details(db, vch_no="SO/1")
    assert so["order_lines"][0] == {"item": "Widget B", "qty": 5.0, "rate": 200.0, "amount": 1000.0}


def test_day_book(db):
    dbk = reports.day_book(db, "2024-06-01", "2024-06-30")
    assert dbk["voucher_count"] == 2
    assert reports.day_book(db, None, None, vch_type=19)["voucher_count"] == 2


def test_trade_analysis(db):
    by_party = reports.trade_analysis(db, "sales", "party", None, None)
    assert by_party["lines"][0] == {"party": "Sharma Traders", "invoices": 1, "gross_incl_tax": 11800.0,
                                    "returns_incl_tax": 1180.0, "net_incl_tax": 10620.0}
    by_item = reports.trade_analysis(db, "sales", "item", None, None)
    assert by_item["lines"][0]["net_qty"] == 36.0
    by_month = reports.trade_analysis(db, "purchase", "month", None, None)
    assert by_month["lines"] == [{"month": "2024-04", "invoices": 1, "gross_incl_tax": 5900.0,
                                  "returns_incl_tax": 0.0, "net_incl_tax": 5900.0}]


def test_chart_and_search(db):
    coa = reports.chart_of_accounts(db, include_ledgers=True)
    names = [n["name"] for n in coa]
    assert names == ["Capital Account", "Current Assets", "Current Liabilities", "Revenue Accounts"]
    hits = reports.search_masters(db, "widget")
    assert {h["name"] for h in hits} == {"Widget A", "Widget B"}
    lm = reports.list_masters(db, "ledger", under_group="Current Assets")
    assert lm["total"] == 3


def test_group_summary(db):
    gs = reports.group_summary(db, "Current Assets", None, None)
    assert gs["total_closing"] == "101,720.00 Dr"


def test_ambiguous_and_missing_names(db):
    with pytest.raises(reports.ReportError):
        reports.ledger_balance(db, "nonexistent", None)
    with pytest.raises(reports.ReportError, match="several"):
        reports.ledger_balance(db, "GST", None)
