"""busy-mcp verify: automatic checks on the user's own data, and five numbers to compare with BUSY."""

from __future__ import annotations

import json
import sqlite3

import pytest

from busy_mcp.cli import main
from busy_mcp.cli.verify import Options, build_report, render_text
from busy_mcp.config import settings_from
from busy_mcp.db import BusyDB, discover_targets
from fixtures.busy_fixture import EXPECTED

PART1 = ("vouchers_balance", "trial_balance", "fast_summary", "bills_vs_ledger", "stock_summary", "opening_balances")


def _checks(report):  # type: ignore[no-untyped-def]
    return {c["key"]: c for c in report["checks"]}


def _numbers(report):  # type: ignore[no-untyped-def]
    return {n["number"]: n for n in report["compare"]}


def _changed(path: str, sql: str) -> dict:  # type: ignore[type-arg]
    """Report on a copy of the company after a change made behind BUSY's back."""
    con = sqlite3.connect(path)
    con.executescript(sql)
    con.close()
    s = settings_from({"backend": "sqlite", "database": path, "fy": 2024})
    with BusyDB(discover_targets(s)[0], s) as d:
        return build_report(d, Options(redact=True))


@pytest.fixture()
def company(monkeypatch, db_path):  # type: ignore[no-untyped-def]
    """Point `busy-mcp verify` at a company through the environment, as a user's setup would."""
    def use(path: str = db_path) -> None:
        monkeypatch.setenv("BUSY_BACKEND", "sqlite")
        monkeypatch.setenv("BUSY_DATABASE", path)
        monkeypatch.setenv("BUSY_FY", "2024")

    use()
    return use


def _run(argv, capsys):  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as ei:
        main(["verify", *argv])
    return ei.value.code, capsys.readouterr().out


def test_every_check_passes_on_the_fixture(db):
    report = build_report(db)
    checks = _checks(report)
    assert {k: checks[k]["status"] for k in PART1} == dict.fromkeys(PART1, "PASS")
    assert {c["status"] for k, c in checks.items() if k not in PART1} == {"INFO"}
    assert (report["result"], report["exit_code"]) == ("PASS", 0)
    assert checks["vouchers_balance"]["data"] == {"vouchers": EXPECTED["active_vouchers"], "account_entries": 17,
                                                  "unbalanced": 0}
    assert checks["fast_summary"]["data"] == {"accounts": 9, "items": 2, "month": "2024-05", "differences": 0}
    assert checks["fast_summary"]["message"].endswith("for the whole year and for May-2024.")  # busiest month
    assert checks["bills_vs_ledger"]["data"] == {"parties": 1, "differences": 0}
    assert checks["stock_summary"]["data"] == {"items": 2, "differences": 0}
    assert checks["opening_balances"]["data"] == {"accounts_with_opening": 3, "items_with_opening": 1,
                                                  "accounts_net_opening": 0.0, "opening_stock_value": 1000.0}
    assert checks["vouchers"]["data"] == {"count": EXPECTED["active_vouchers"], "first_date": "2024-04-10",
                                          "last_date": "2025-03-25"}
    assert checks["cancelled_vouchers"]["data"] == {"count": 1, "excluded": True}  # S/2
    assert checks["masters"]["data"]["Account"] == EXPECTED["ledger_count"]


def test_five_numbers_equal_the_fixtures_known_values(db):
    n = _numbers(build_report(db))
    assert {k: v["status"] for k, v in n.items()} == dict.fromkeys(range(1, 6), "OK")

    tb = n[1]["values"]  # closing Dr: bank 82,100 + cash 16,500 + Sharma 2,620 + purchase 5,000 + rent 3,500 + GST 900
    assert (tb["total_debit"], tb["total_credit"], tb["difference"]) == (110620.0, 110620.0, "0.00")
    assert {"group": "Current Assets", "closing": "101,220.00 Dr"} in tb["main_groups"]  # = total assets

    party = n[2]["values"]
    assert (party["party"], party["chosen"]) == ("Sharma Traders", "the customer with the largest balance")
    assert (party["opening"], party["total_debit"], party["total_credit"], party["closing"]) == (
        "0.00", 11800.0, 9180.0, EXPECTED["sharma_closing"])

    sales = {m["month"]: m for m in n[3]["values"]["months"]}
    assert list(sales) == [f"2024-{m:02d}" for m in range(4, 13)] + ["2025-01", "2025-02", "2025-03"]
    assert sales["2024-05"] == {"month": "2024-05", "invoices": 1, "sales_incl_tax": 11800.0,
                                "sales_before_tax": 10000.0, "returns_incl_tax": 0.0}
    assert sales["2024-07"]["returns_incl_tax"] == 1180.0  # CN/1
    assert sales["2024-08"]["sales_incl_tax"] == 0.0  # the cancelled S/2 is not counted
    total = n[3]["values"]["total"]
    assert total["sales_incl_tax"] - total["returns_incl_tax"] == 10620.0  # the dashboard's net sales

    stock = n[4]["values"]
    assert (stock["item"], stock["chosen"], stock["unit"]) == ("Widget A", "the item with the most movement", "Pcs.")
    assert (stock["opening_qty"], stock["inward_qty"], stock["outward_qty"], stock["closing_qty"]) == (
        EXPECTED["widget_a"])

    bills = n[5]["values"]
    assert (bills["party"], bills["nature"], bills["bill_count"], bills["total_pending"], bills["ledger_balance"]) == (
        "Sharma Traders", "receivable", 1, 2620.0, 2620.0)
    assert bills["bills"] == [{"bill_no": "S/1", "date": "2024-05-05", "due_date": "2024-06-04", "pending": 2620.0,
                               "overdue_days": 300}]


def test_party_and_item_overrides(db):
    report = build_report(db, Options(party="Gupta Suppliers", item="Widget B"))
    n = _numbers(report)
    assert (n[2]["values"]["party"], n[2]["values"]["closing"]) == ("Gupta Suppliers", "0.00")
    assert n[2]["values"]["chosen"] == "the account you chose"
    assert n[4]["values"]["closing_qty"] == EXPECTED["widget_b_closing"]
    assert n[5]["status"] == "INFO" and "no pending bills" in n[5]["note"]  # P/1 was paid by PY/2
    assert report["exit_code"] == 0


def test_an_unknown_party_is_a_plain_failure(db):
    report = build_report(db, Options(party="Nobody Such"))
    n = _numbers(report)
    assert n[2]["status"] == n[5]["status"] == "FAIL"
    assert n[2]["note"].startswith('could not use --party "Nobody Such": No Account named \'Nobody Such\'')
    assert report["exit_code"] == 1


def test_redact_hides_names(db):
    report = build_report(db, Options(redact=True))
    shared = render_text(report) + json.dumps(report)
    for name in ("Sharma", "Gupta", "Widget", "Demo Traders"):
        assert name not in shared
    n = _numbers(report)
    assert (n[2]["values"]["party"], n[4]["values"]["item"], n[5]["values"]["party"]) == ("Party 1", "Item 1",
                                                                                        "Party 1")
    assert report["company"]["name"] == "Company 1" and report["redacted"] is True
    assert "Closing balance of Party 1 (the customer with the largest balance)" in shared


def test_time_limit_stops_one_by_one_checks_without_failing(db):
    report = build_report(db, Options(time_limit=0))
    checks = _checks(report)
    assert checks["fast_summary"]["status"] == checks["stock_summary"]["status"] == "INFO"
    assert "time limit" in checks["fast_summary"]["message"]
    assert checks["bills_vs_ledger"]["status"] == "PASS" and "Suppliers were not checked" in (
        checks["bills_vs_ledger"]["message"])
    assert report["exit_code"] == 0


def test_cli_text_report_is_printed_and_saved(company, capsys, tmp_path):
    saved = tmp_path / "verify.txt"
    code, out = _run(["--out", str(saved)], capsys)
    assert code == 0
    assert "[PASS] Every voucher balances" in out and "[FAIL]" not in out
    assert "In BUSY:    Trial Balance (Display menu), as on 31-Mar-2025" in out
    assert "Result: every check passed." in out
    assert out.startswith(saved.read_text(encoding="utf-8"))  # the file is exactly what was printed


def test_cli_json_is_valid(company, capsys, tmp_path):
    saved = tmp_path / "verify.json"
    code, out = _run(["--json", "--redact", "--out", str(saved)], capsys)
    report = json.loads(out)
    assert code == 0 and (report["result"], report["exit_code"], report["redacted"]) == ("PASS", 0, True)
    assert [c["key"] for c in report["checks"]][:6] == list(PART1)
    assert [n["number"] for n in report["compare"]] == [1, 2, 3, 4, 5]
    assert json.loads(saved.read_text(encoding="utf-8")) == report


def test_unbalanced_voucher_fails(company, writable_db_path, capsys):
    con = sqlite3.connect(writable_db_path)  # P/1's purchase line changed: debits now exceed credits by 100
    con.execute("UPDATE Tran2 SET Value1 = -5100 WHERE VchCode = 1 AND SrNo = 1 AND RecType = 1")
    con.commit()
    con.close()
    company(writable_db_path)
    code, out = _run([], capsys)
    assert code == 1
    assert "[FAIL] Every voucher balances: 1 voucher does not balance" in out
    assert "Result: problems found." in out
    code, out = _run(["--json"], capsys)
    checks = _checks(json.loads(out))
    assert code == 1 and checks["vouchers_balance"]["status"] == "FAIL"
    assert checks["vouchers_balance"]["details"] == ["P/1 (Purchase, 10-Apr-2024): debits are 100.00 more than credits"]
    assert checks["trial_balance"]["status"] == "FAIL" and "100.00 Dr comes from vouchers" in (
        checks["trial_balance"]["message"])
    assert checks["fast_summary"]["status"] == "PASS"  # the summary and the vouchers still agree with each other


@pytest.mark.parametrize(("sql", "expected", "phrase"), [
    pytest.param("UPDATE Folio1 SET D1 = 90000 WHERE MasterCode = 201 AND MasterType = 2",
                 {"trial_balance": "WARN", "opening_balances": "WARN"},
                 "opening balances were not carried forward", id="openings-do-not-tally"),
    pytest.param("UPDATE Folio1 SET D1 = 101000 WHERE MasterCode = 201 AND MasterType = 2",
                 {"trial_balance": "WARN", "opening_balances": "PASS"},
                 "exactly the value of your opening stock (1,000.00)", id="opening-stock-explains-difference"),
    pytest.param("DELETE FROM Tran3 WHERE VchCode = 3", {"bills_vs_ledger": "WARN"},
                 "Party 1 (customer): pending bills 10,620.00, ledger balance 2,620.00", id="receipt-not-matched"),
    pytest.param("UPDATE Tran2 SET MasterCode1 = 999 WHERE VchCode = 4 AND SrNo = 1 AND RecType = 1",
                 {"trial_balance": "FAIL"}, "posted to 1 account code missing from the list of accounts",
                 id="entry-for-unknown-account"),
])
def test_problems_are_found_and_explained(writable_db_path, sql, expected, phrase):
    report = _changed(writable_db_path, sql)
    checks = _checks(report)
    assert {k: checks[k]["status"] for k in PART1} == {k: expected.get(k, "PASS") for k in PART1}
    assert phrase in json.dumps(report)
    assert "Sharma" not in json.dumps(report)
    assert report["exit_code"] == (1 if "FAIL" in expected.values() else 0)


def test_checks_are_skipped_when_this_busy_version_lacks_their_tables(writable_db_path):
    report = _changed(writable_db_path, "DROP TABLE Tran3; DROP TABLE Folio1;")
    checks, n = _checks(report), _numbers(report)
    assert checks["bills_vs_ledger"]["status"] == checks["opening_balances"]["status"] == "INFO"
    assert checks["bills_vs_ledger"]["message"].startswith("skipped: this BUSY version has no Tran3 table")
    assert {k: checks[k]["status"] for k in ("vouchers_balance", "trial_balance", "fast_summary", "stock_summary")} == (
        dict.fromkeys(("vouchers_balance", "trial_balance", "fast_summary", "stock_summary"), "PASS"))
    assert n[5]["status"] == "INFO" and "bill-by-bill" in n[5]["note"]
    assert n[4]["values"]["closing_qty"] == 14  # Widget A without its opening stock of 10
    assert report["exit_code"] == 0


def test_fast_summary_differences_fail(db, monkeypatch):
    from busy_mcp.reports._index import MovementIndex

    real = MovementIndex.range

    def one_rupee_off(self, code, start, end):  # type: ignore[no-untyped-def]
        r = real(self, code, start, end)
        return (r[0] + 1.0, *r[1:]) if code == 204 else r

    monkeypatch.setattr(MovementIndex, "range", one_rupee_off)
    report = build_report(db)
    fast = _checks(report)["fast_summary"]
    assert fast["status"] == "FAIL" and report["exit_code"] == 1
    assert fast["details"][0] == ("Sharma Traders, the whole year: quick totals debit 11,801.00 / credit 9,180.00; "
                                  "vouchers debit 11,800.00 / credit 9,180.00")


def test_cli_company_and_year(company, capsys):
    code, out = _run(["--company", "COMP0001", "--fy", "2024-25"], capsys)
    assert code == 0 and "financial year 2024-25 (01-Apr-2024 to 31-Mar-2025)" in out
    code, out = _run(["--fy", "2023"], capsys)
    assert code == 1 and "[FAIL] Cannot find your BUSY data: FY 2023 not found for COMP0001" in out
    with pytest.raises(SystemExit) as ei:
        main(["verify", "--fy", "24"])
    assert ei.value.code == 2  # argparse: not a year


def test_cli_without_busy_data_still_answers_in_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BUSY_BACKEND", "access")
    monkeypatch.setenv("BUSY_DATA_DIR", str(tmp_path / "missing"))
    code, out = _run(["--json"], capsys)
    report = json.loads(out)
    assert code == 1 and report["result"] == "FAIL"
    assert report["error"].startswith("Cannot find your BUSY data: BUSY data folder not found")
