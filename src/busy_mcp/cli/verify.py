"""``busy-mcp verify``: show, on your own BUSY data, that busy-mcp's numbers can be trusted.

Part 1 runs consistency checks on the company's real data and says PASS, WARN or FAIL for each, in plain
words. Part 2 works out the five numbers of the production checklist (docs/production-checklist.md) and says
where to find each one in BUSY, so they can be compared with BUSY's own screens.

Everything goes through the report functions the AI uses. The little SQL here reads voucher lines directly,
on purpose, to compare them with busy-mcp's in-memory summary (the movement index); it keeps to the subset
that runs on Access, SQL Server and SQLite (see reports/_common.py). Like the rest of busy-mcp it only reads.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import textwrap
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .. import __version__, reports
from ..config import load_settings
from ..db import BusyDB, Target, choose_target, discover_targets, fy_label
from ..errors import BusyConnectionError, BusyMCPError, ReportError
from ..reports._common import (
    codes_under_groups,
    drcr,
    find_master,
    fy_range,
    int_list,
    ledger_index,
    ledger_movements,
    next_day,
    opening_balances,
    r2,
    vt,
)
from ..reports.bills import cancelled_vouchers
from ..reports.inventory import item_index, item_inward_sign, item_rectypes
from ..schema import MT_ITEM, MT_LEDGER, MT_UNIT, RT_LEDGER, VT_SALES_RETURN

SAMPLE = 25  # accounts and items compared one by one
TIME_LIMIT = 60.0  # seconds per check; checks that compare one by one stop at the limit and say so
MONEY_STEP = 0.01  # money differences count from Rs 0.01 ...
QTY_STEP = 0.001  # ... and quantity differences from 0.001
SHOWN = 0.005  # reports round to two decimals, so a figure they show can be this far from the exact one
ROUNDING = 1.0  # entries off by less than Rs 1 in all look like paise rounding (WARN); more is an error (FAIL)
LISTED = 5  # problems named one by one; the rest are counted
BILLS_SHOWN = 20  # bills listed for number 5
ALL = 1_000_000  # page size meaning "everything"
WIDTH = 100  # text output is wrapped to this width, so it reads well in a Windows console
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ----------------------------------------------------------------------------- formatting


def _day(value: Any) -> str:
    """A date as 31-Mar-2025, which nobody misreads; '?' when there is none."""
    if not value:
        return "?"
    try:
        d = value if isinstance(value, _dt.date) else _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _month(ym: str) -> str:
    """'2024-05' -> 'May-2024'."""
    year, month = ym.split("-")[:2]
    return f"{_MONTHS[int(month) - 1]}-{year}"


def _months(start: _dt.date, end: _dt.date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _amt(x: Any) -> str:
    return f"{r2(x):,.2f}"


def _qty(x: Any) -> str:
    text = f"{round(float(x or 0), 3) + 0.0:,.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _n(count: int, word: str, plural: str = "") -> str:
    return f"{count:,} {word if count == 1 else plural or word + 's'}"


def _differs(a: float, b: float, step: float) -> bool:
    """True when two figures are at least half a step apart, so they still differ once rounded to the step."""
    return abs(a - b) >= step / 2


# ----------------------------------------------------------------------------- building blocks


def _direct_item_qty(db: BusyDB, code: int, start: _dt.date | None, end: _dt.date, rectypes: list[int],
                     inward_sign: int) -> tuple[float, float]:
    """Inward and outward quantity of one item for start..end (start None: from the beginning), read straight
    from its voucher lines instead of busy-mcp's in-memory summary."""
    sql = ("SELECT SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS pq, SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS nq "
           "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
           f"WHERE t2.RecType IN ({int_list(rectypes)}) AND t2.MasterCode1 = ? AND t1.[Date] < ?"
           + db.active_voucher_filter("t1"))
    params: list[Any] = [code, db.dparam(next_day(end))]
    if start is not None:
        sql += " AND t1.[Date] >= ?"
        params.append(db.dparam(start))
    rows = db.query(sql, params)
    pq = float(rows[0].get("pq") or 0) if rows else 0.0
    nq = float(rows[0].get("nq") or 0) if rows else 0.0
    return (pq, nq) if inward_sign > 0 else (nq, pq)


class Redactor:
    """Stable placeholders ("Party 1", "Item 2") instead of names, for a report that will be shared."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._names: dict[tuple[str, str], str] = {}
        self._count: Counter[str] = Counter()

    def __call__(self, kind: str, name: str | None) -> str:
        text = name or ""
        if not self.enabled or not text:
            return text
        key = (kind, text)
        if key not in self._names:
            self._count[kind] += 1
            self._names[key] = f"{kind} {self._count[kind]}"
        return self._names[key]


class _Budget:
    def __init__(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds

    def left(self) -> bool:
        return time.monotonic() < self._deadline


@dataclass
class Options:
    party: str | None = None  # account for numbers 2 and 5 instead of the automatic choice
    item: str | None = None  # item for number 4
    redact: bool = False
    time_limit: float = TIME_LIMIT


@dataclass
class Outcome:
    """What one check found."""

    status: str  # PASS | WARN | FAIL | INFO
    message: str
    details: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Check:
    key: str
    title: str
    status: str
    message: str
    details: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class Number:
    """One of the five numbers to compare with BUSY."""

    number: int
    key: str
    title: str
    status: str = "OK"  # OK | INFO (not available in this company) | FAIL (an override could not be used)
    where_in_busy: str = ""
    must_match: str = ""
    lines: list[str] = field(default_factory=list)  # busy-mcp's figures, as printed
    values: dict[str, Any] = field(default_factory=dict)  # the same figures for --json
    note: str = ""


# ----------------------------------------------------------------------------- the checks


class Verifier:
    """Runs the checks and works out the five numbers for one company and financial year."""

    def __init__(self, db: BusyDB, options: Options | None = None, targets: list[Target] | None = None) -> None:
        self.db = db
        self.options = options or Options()
        self.targets = targets or [db.target]
        self.name = Redactor(self.options.redact)
        self.budget = _Budget(self.options.time_limit)
        self.start, self.end = fy_range(db)
        self.masters = db.masters()
        self.debtors = codes_under_groups(db, db.settings.debtor_groups)
        self.creditors = codes_under_groups(db, db.settings.creditor_groups)
        self._bills: dict[str, dict[str, Any] | str] = {}

    # --------------------------------------------------------- names and shared figures

    @property
    def fy(self) -> str:
        return fy_label(self.start.year) or ""

    def _is(self, code: int, master_type: int) -> bool:
        m = self.masters.get(code)
        return m is not None and m.type == master_type

    def account(self, code: int) -> str:
        m = self.masters.get(code)
        kind = "Party" if code in self.debtors or code in self.creditors else "Account"
        return self.name(kind, m.name if m else f"#{code}")

    def item(self, code: int) -> str:
        m = self.masters.get(code)
        return self.name("Item", m.name if m else f"#{code}")

    def missing(self, table: str, columns: tuple[str, ...] = ()) -> str | None:
        """Why this BUSY version can't support a check (plain words), or None when it can."""
        if not self.db.has_table(table):
            return f"this BUSY version has no {table} table, which this check needs."
        absent = [c for c in columns if not self.db.has_column(table, c)]
        if absent:
            return f"this BUSY version has no {', '.join(absent)} column in {table}, which this check needs."
        return None

    @cached_property
    def overview(self) -> dict[str, Any]:
        return reports.company_overview(self.db)

    @cached_property
    def tb(self) -> dict[str, Any]:
        return reports.trial_balance(self.db, None, None, ledger_level=False)

    @cached_property
    def opening(self) -> dict[str, Any]:
        """Net opening balance of all accounts (debit positive) and opening stock value, from Folio1."""
        accounts = {c: -v["d1"] for c, v in opening_balances(self.db).items() if self._is(c, MT_LEDGER)}
        items = {c: v for c, v in opening_balances(self.db, MT_ITEM).items() if self._is(c, MT_ITEM)}
        return {"accounts_net": sum(accounts.values()), "stock_value": sum(v["d3"] for v in items.values()),
                "accounts": sum(1 for v in accounts.values() if abs(v) >= MONEY_STEP / 2),
                "items": sum(1 for v in items.values() if abs(v["d1"]) >= QTY_STEP / 2)}

    @cached_property
    def account_activity(self) -> dict[int, tuple[float, ...]]:
        moves = ledger_index(self.db).all_ranges(self.start, self.end)
        return {c: v for c, v in moves.items() if self._is(c, MT_LEDGER)}

    @cached_property
    def item_activity(self) -> dict[int, tuple[float, ...]]:
        moves = item_index(self.db).all_ranges(self.start, self.end)
        return {c: v for c, v in moves.items() if self._is(c, MT_ITEM)}

    @cached_property
    def stock_rows(self) -> dict[str, dict[str, Any]]:
        return {r["item"]: r for r in reports.stock_summary(self.db, None, None, include_zero=True, limit=ALL)["items"]}

    @cached_property
    def item_name_count(self) -> Counter[str]:
        return Counter(m.name for m in self.masters.values() if m.type == MT_ITEM)

    def busiest_accounts(self) -> list[int]:
        a = self.account_activity
        return sorted(a, key=lambda c: (-(a[c][0] + a[c][1]), c))[:SAMPLE]

    def busiest_items(self) -> list[int]:
        """Most movement by value, then by quantity (values compare across units, quantities don't)."""
        a = self.item_activity
        return sorted(a, key=lambda c: (-(a[c][2] + a[c][3]), -(a[c][0] + a[c][1]), c))[:SAMPLE]

    def busiest_month(self, accounts: list[int], items: list[int]) -> tuple[str, _dt.date, _dt.date] | None:
        if accounts:
            months = ledger_index(self.db).monthly(accounts, self.start, self.end)
        elif items:
            months = item_index(self.db).monthly(items, self.start, self.end)
        else:
            return None
        best = max(sorted(months), key=lambda k: sum(months[k][:2]))  # the earliest one on a tie
        y, m = (int(x) for x in best.split("-"))
        last = _dt.date(y + m // 12, m % 12 + 1, 1) - _dt.timedelta(days=1)
        return _month(best), max(_dt.date(y, m, 1), self.start), min(last, self.end)

    def bills(self, nature: str) -> dict[str, Any] | str:
        """Bill-wise outstanding of receivables or payables, or why it isn't available."""
        if nature not in self._bills:
            try:
                self._bills[nature] = reports.bills_outstanding(self.db, nature, limit=ALL)
            except ReportError as exc:
                self._bills[nature] = str(exc)
        return self._bills[nature]

    def unit(self, code: int) -> str:
        if not self.db.has_column("Master1", "CM1"):
            return ""
        rows = self.db.query("SELECT CM1 FROM Master1 WHERE Code = ?", (code,))
        unit = int(rows[0].get("cm1") or 0) if rows else 0
        return self.masters[unit].name if self._is(unit, MT_UNIT) else ""

    # ----------------------------------------------------------------------- checks

    def run_checks(self) -> Iterator[Check]:
        plan: list[tuple[str, str, Callable[[], Outcome]]] = [
            ("vouchers_balance", "Every voucher balances", self.check_vouchers),
            ("trial_balance", "Trial balance tallies", self.check_trial_balance),
            ("fast_summary", "Fast summary matches the vouchers", self.check_fast_summary),
            ("bills_vs_ledger", "Pending bills match ledger balances", self.check_bills),
            ("stock_summary", "Stock summary adds up", self.check_stock),
            ("opening_balances", "Opening balances carried forward", self.check_openings),
            ("vouchers", "Vouchers", self.info_vouchers),
            ("cancelled_vouchers", "Cancelled vouchers", self.info_cancelled),
            ("masters", "Masters", self.info_masters),
        ]
        for key, title, check in plan:
            started = time.perf_counter()
            self.budget = _Budget(self.options.time_limit)
            try:
                outcome = check()
            except BusyConnectionError:
                raise
            except ReportError as exc:
                outcome = Outcome("INFO", f"skipped: {exc}")
            except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
                outcome = Outcome("FAIL", f"this check could not run ({type(exc).__name__}: {exc}). Please report it.")
            yield Check(key, title, outcome.status, outcome.message, outcome.details, outcome.data,
                        round(time.perf_counter() - started, 3))

    def check_vouchers(self) -> Outcome:
        """BUSY never saves a voucher whose debits and credits differ, so the account lines (Tran2 RecType 1)
        of every voucher must add up to zero. A voucher that doesn't means busy-mcp is misreading a line."""
        why = self.missing("Tran2", ("VchCode", "RecType", "Value1"))
        if why:
            return Outcome("INFO", f"skipped: {why}")
        db = self.db
        vouchers = int(self.overview["vouchers"].get("n") or 0)
        if not vouchers:
            return Outcome("INFO", f"there are no vouchers in {self.fy} yet.")
        lines = ("FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
                 f"WHERE t2.RecType = {RT_LEDGER}{db.active_voucher_filter('t1')}")
        entries = int(db.query(f"SELECT COUNT(*) AS n {lines}")[0]["n"] or 0)
        rows = db.query(f"SELECT t2.VchCode AS vc, SUM(t2.Value1) AS s {lines} GROUP BY t2.VchCode "
                        f"HAVING ABS(SUM(t2.Value1)) >= {MONEY_STEP / 2}")
        off = sorted(((int(r["vc"]), -float(r["s"] or 0)) for r in rows if r.get("vc") is not None),
                     key=lambda x: (-abs(x[1]), x[0]))  # debit positive, biggest first
        data = {"vouchers": vouchers, "account_entries": entries, "unbalanced": len(off)}
        if not off:
            return Outcome("PASS", f"debit equals credit in every voucher ({_n(vouchers, 'voucher')}, "
                                   f"{_n(entries, 'account entry', 'account entries')}).", data=data)
        listed = int_list(c for c, _ in off[:LISTED])
        heads = {int(h["vchcode"]): h
                 for h in db.query(f"SELECT VchCode, VchType, VchNo, [Date] FROM Tran1 WHERE VchCode IN ({listed})")}
        details = []
        for code, diff in off[:LISTED]:
            h = heads.get(code, {})
            more, less = ("debits", "credits") if diff > 0 else ("credits", "debits")
            details.append(f"{h.get('vchno') or f'voucher code {code}'} ({vt(db, h.get('vchtype'))}, "
                           f"{_day(h.get('date'))}): {more} are {_amt(abs(diff))} more than {less}")
        if len(off) > LISTED:
            details.append(f"... and {_n(len(off) - LISTED, 'more voucher')}")
        fail = max(abs(d) for _, d in off) >= ROUNDING
        message = (f"{_n(len(off), 'voucher')} {'does' if len(off) == 1 else 'do'} not balance (debits and credits "
                   f"differ by {drcr(sum(d for _, d in off))} in all). ")
        message += ("busy-mcp may be misreading this BUSY version, so reports that use these vouchers can be wrong."
                    if fail else "Each is off by less than Rs 1 (paise rounding); reports may differ from BUSY by "
                                 "these paise.")
        return Outcome("FAIL" if fail else "WARN", message, details, data)

    def check_trial_balance(self) -> Outcome:
        """The trial balance (the report the AI uses) must tally. When it doesn't, say where the difference
        comes from: the opening stock (expected), this year's opening balances, or voucher entries (an error)."""
        totals = self.tb["totals"]
        on = _day(self.end)
        data = {k: totals[k] for k in ("closing_dr", "closing_cr", "net_difference")}
        if totals["net_difference"] == "0.00":
            return Outcome("PASS", f"total debit {_amt(totals['closing_dr'])} = total credit "
                                   f"{_amt(totals['closing_cr'])} on {on}.", data=data)
        head = (f"total debit {_amt(totals['closing_dr'])} and total credit {_amt(totals['closing_cr'])} on {on} "
                f"differ by {totals['net_difference']}.")
        moved = ledger_movements(self.db, None, self.end)
        unbalanced = sum(dr - cr for dr, cr in moved.values())
        stray = {c: dr - cr for c, (dr, cr) in moved.items() if not self._is(c, MT_LEDGER)}
        from_entries = unbalanced - sum(stray.values())  # = trial balance difference - opening balances
        if _differs(from_entries, 0.0, MONEY_STEP):
            parts = []
            if _differs(unbalanced, 0.0, MONEY_STEP):
                parts.append(f"{drcr(unbalanced)} comes from vouchers whose debits and credits differ "
                             "(see the first check)")
            if _differs(sum(stray.values()), 0.0, MONEY_STEP):
                parts.append(f"{drcr(-sum(stray.values()))} comes from entries posted to "
                             f"{_n(len(stray), 'account code')} missing from the list of accounts")
            if not parts:
                parts.append(f"{drcr(from_entries)} comes from voucher entries")
            fail = abs(from_entries) >= ROUNDING
            return Outcome("FAIL" if fail else "WARN", f"{head} {'; '.join(parts)}.", data=data)
        o = self.opening
        if _differs(o["stock_value"], 0.0, MONEY_STEP) and not _differs(o["accounts_net"], -o["stock_value"],
                                                                         MONEY_STEP):
            return Outcome("WARN", f"{head} That is exactly the value of your opening stock "
                                   f"({_amt(o['stock_value'])}): busy-mcp's trial balance lists accounts only and "
                                   "leaves stock out, so this is expected.", data=data)
        return Outcome("WARN", f"{head} The difference comes from this year's opening balances (see 'Opening "
                               "balances carried forward' below); the vouchers themselves balance.", data=data)

    def check_fast_summary(self) -> Outcome:
        """busy-mcp answers from an in-memory summary of daily totals (the movement index). For the busiest
        accounts and items it must give the same totals as reading the voucher lines directly, over the whole
        year and over the busiest month."""
        why = self.missing("Tran2", ("VchCode", "RecType", "MasterCode1", "Value1"))
        if why:
            return Outcome("INFO", f"skipped: {why}")
        db = self.db
        accounts = self.busiest_accounts()
        items = self.busiest_items() if db.has_column("Tran2", "Value3") else []  # the item summary needs Value3
        if not accounts and not items:
            return Outcome("INFO", f"there are no account or stock entries in {self.fy} yet, so there is nothing "
                                   "to compare.")
        periods = [("the whole year", self.start, self.end)]
        month = self.busiest_month(accounts, items)
        if month:
            periods.append(month)
        rectypes, sign = (item_rectypes(db)[0], item_inward_sign(db)[0]) if items else ([], 1)
        problems: list[str] = []
        done = {"account": 0, "item": 0}
        try:
            with BusyDB(db.target, db.settings) as direct:  # a second connection with no summary: reads directly
                for kind, codes in (("account", accounts), ("item", items)):
                    for code in codes:
                        if not self.budget.left():
                            break
                        for label, start, end in periods:
                            fast: tuple[float, ...]
                            slow: tuple[float, ...]
                            if kind == "account":
                                fast = ledger_movements(db, start, end, ledger=code).get(code, (0.0, 0.0))
                                slow = ledger_movements(direct, start, end, ledger=code).get(code, (0.0, 0.0))
                                fmt, step, sides, who = _amt, MONEY_STEP, ("debit", "credit"), self.account(code)
                            else:  # the item summary was built (and cached) when the busiest items were picked
                                fast = item_index(db).range(code, start, end)[:2]
                                slow = _direct_item_qty(direct, code, start, end, rectypes, sign)
                                fmt, step, sides, who = _qty, QTY_STEP, ("in", "out"), self.item(code)
                            if any(_differs(a, b, step) for a, b in zip(fast, slow, strict=True)):
                                problems.append(f"{who}, {label}: quick totals {sides[0]} {fmt(fast[0])} / "
                                                f"{sides[1]} {fmt(fast[1])}; vouchers {sides[0]} {fmt(slow[0])} / "
                                                f"{sides[1]} {fmt(slow[1])}")
                        done[kind] += 1
        except BusyConnectionError as exc:
            return Outcome("INFO", f"skipped: a second read-only connection, needed to read the vouchers directly, "
                                   f"could not be opened ({exc}).")
        checked, total = done["account"] + done["item"], len(accounts) + len(items)
        data = {"accounts": done["account"], "items": done["item"],
                "month": month[1].isoformat()[:7] if month else None, "differences": len(problems)}
        if problems:
            details = problems[:LISTED] + ([f"... and {len(problems) - LISTED} more"] if len(problems) > LISTED else [])
            return Outcome("FAIL", f"busy-mcp's quick totals differ from a direct reading of the vouchers in "
                                   f"{_n(len(problems), 'case')}. Answers built from them would be wrong; please "
                                   "report this.", details, data)
        if not checked:
            return Outcome("INFO", f"skipped: the {self.options.time_limit:g}-second time limit was reached before "
                                   "anything was compared.", data=data)
        what = " and ".join(_n(done[k], f"busiest {k}") for k in ("account", "item") if done[k])
        message = (f"busy-mcp's quick totals agree with a direct reading of the vouchers for the {what}, "
                   f"for {' and for '.join(label for label, _, _ in periods)}.")
        if checked < total:
            message += (f" (Stopped after {checked} of {total} because of the {self.options.time_limit:g}-second "
                        "time limit.)")
        return Outcome("PASS", message, data=data)

    def check_bills(self) -> Outcome:
        """For each party with pending bills, the pending bills should add up to the ledger balance."""
        why = self.missing("Tran3", ("Method", "MasterCode1", "Value1"))
        if why:
            return Outcome("INFO", f"skipped: {why} Bill-by-bill details are not kept in this BUSY version.")
        parties: list[tuple[str, dict[str, Any]]] = []
        reasons: list[str] = []
        late = ""
        for nature in ("receivable", "payable"):
            if nature == "payable" and not self.budget.left():
                late = " (Suppliers were not checked because of the time limit.)"
                break
            out = self.bills(nature)
            if isinstance(out, dict):
                parties += [(nature, p) for p in out["parties"]]
            else:
                reasons.append(out)
        if not parties:
            why = f" {' '.join(reasons)}" if reasons else ""
            return Outcome("INFO", f"no party has pending bills on {_day(self.end)} (or bill-by-bill details are not "
                                   f"kept), so there is nothing to compare.{why}{late}")
        off = sorted(((n, p) for n, p in parties
                      if _differs(p["pending_bills_total"], p["ledger_balance"], MONEY_STEP)),
                     key=lambda x: -abs(x[1]["on_account_or_unallocated"]))
        data = {"parties": len(parties), "differences": len(off)}
        who = "the only party" if len(parties) == 1 else f"all {len(parties):,} parties"
        if not off:
            return Outcome("PASS", f"for {who} with pending bills on {_day(self.end)}, the bills add up to the "
                                   f"ledger balance.{late}", data=data)
        details = []
        for nature, p in off[:LISTED]:
            gap = p["on_account_or_unallocated"]  # ledger balance - pending bills
            role = "customer" if nature == "receivable" else "supplier"
            details.append(f"{self.name('Party', p['party'])} ({role}): pending bills "
                           f"{_amt(p['pending_bills_total'])}, ledger balance {_amt(p['ledger_balance'])}; "
                           + (f"{_amt(gap)} of the balance is not matched to any bill" if gap > 0 else
                              f"the bills come to {_amt(-gap)} more than the balance"))
        if len(off) > LISTED:
            details.append(f"... and {_n(len(off) - LISTED, 'more party', 'more parties')}")
        some = who if len(off) == len(parties) else f"{len(off):,} of the {len(parties):,} parties"
        return Outcome("WARN", f"for {some} with pending bills, the bills don't add up to the ledger balance. The "
                               "difference is usually money received or paid without being matched to a bill; compare "
                               f"with the party's bill-wise report in BUSY.{late}", details, data)

    def check_stock(self) -> Outcome:
        """For the busiest items, the stock summary's opening, inward, outward and closing quantities must
        match the opening stock plus the item's voucher lines, read directly."""
        why = self.missing("Tran2", ("VchCode", "RecType", "MasterCode1", "Value1", "Value3"))
        if why:
            return Outcome("INFO", f"skipped: {why}")
        if not any(m.type == MT_ITEM for m in self.masters.values()):
            return Outcome("INFO", "this company has no items, so there is no stock to check.")
        sample = self.busiest_items()
        if not sample:
            return Outcome("INFO", f"no item has stock movements in {self.fy} yet.")
        db = self.db
        rectypes, sign = item_rectypes(db)[0], item_inward_sign(db)[0]
        folio = opening_balances(db, MT_ITEM)
        day_before = self.start - _dt.timedelta(days=1)
        problems: list[str] = []
        done, shared, stopped = 0, 0, False
        for code in sample:
            if not self.budget.left():
                stopped = True
                break
            name = self.masters[code].name
            row = self.stock_rows.get(name) if self.item_name_count[name] == 1 else None
            if row is None:  # two items share this name, so the summary's line can't be told apart
                shared += 1
                continue
            before_in, before_out = _direct_item_qty(db, code, None, day_before, rectypes, sign)
            inward, outward = _direct_item_qty(db, code, self.start, self.end, rectypes, sign)
            opening = folio.get(code, {}).get("d1", 0.0) + before_in - before_out
            exact = (opening, inward, outward, opening + inward - outward)
            shown = tuple(float(row[k] or 0) for k in ("opening_qty", "inward_qty", "outward_qty", "closing_qty"))
            if any(abs(s - e) > SHOWN + QTY_STEP / 2 for s, e in zip(shown, exact, strict=True)):
                problems.append(f"{self.item(code)}: stock summary {_qty(shown[0])} + {_qty(shown[1])} in - "
                                f"{_qty(shown[2])} out = {_qty(shown[3])}; vouchers {_qty(exact[0])} + "
                                f"{_qty(exact[1])} in - {_qty(exact[2])} out = {_qty(exact[3])}")
            done += 1
        data = {"items": done, "differences": len(problems)}
        if problems:
            details = problems[:LISTED] + ([f"... and {len(problems) - LISTED} more"] if len(problems) > LISTED else [])
            return Outcome("FAIL", f"for {_n(len(problems), 'item')} the stock summary disagrees with the item's "
                                   "voucher lines, so busy-mcp's stock answers would be wrong for them; please report "
                                   "this.", details, data)
        limit = f"the {self.options.time_limit:g}-second time limit"
        if not done:
            reason = f"{limit} was reached" if stopped else "the busiest items share their names with other items"
            return Outcome("INFO", f"skipped: {reason} before any item could be compared.", data=data)
        message = (f"for the {_n(done, 'busiest item')}, opening + inward - outward = closing in the stock summary, "
                   "and each figure matches the opening stock plus the item's voucher lines read directly.")
        if shared:
            message += f" ({_n(shared, 'item')} sharing a name with another item could not be compared.)"
        if stopped:
            message += f" (Stopped after {done} of {len(sample)} because of {limit}.)"
        return Outcome("PASS", message, data=data)

    def check_openings(self) -> Outcome:
        """This year's opening balances should be in place and tally, either by themselves or with the opening
        stock (BUSY carries profit, stock included, into the accounts). Uses the trial balance's own warning."""
        why = self.missing("Folio1", ("MasterCode", "MasterType", "D1"))
        if why:
            return Outcome("INFO", f"skipped: {why}")
        o = self.opening
        data = {"accounts_with_opening": o["accounts"], "items_with_opening": o["items"],
                "accounts_net_opening": r2(o["accounts_net"]), "opening_stock_value": r2(o["stock_value"])}
        if not o["accounts"] and not o["items"]:
            company = (self.db.target.company or "").upper()
            earlier = sorted(t.fy for t in self.targets if t.fy and self.db.target.fy and t.fy < self.db.target.fy
                             and (t.company or "").upper() == company)
            if earlier:
                return Outcome("WARN", f"no account or item has an opening balance in {self.fy}, although BUSY also "
                                       f"has {fy_label(earlier[-1])} for this company. Last year's closing balances "
                                       "were probably not carried forward.", data=data)
            return Outcome("INFO", "no account or item has an opening balance, and no earlier year was found for "
                                   "this company, so there was nothing to carry forward.", data=data)
        have = (f"{_n(o['accounts'], 'account')} and {_n(o['items'], 'item')} "
                f"{'has' if o['accounts'] + o['items'] == 1 else 'have'} opening balances")
        if not _differs(o["accounts_net"], 0.0, MONEY_STEP):
            return Outcome("PASS", f"{have}, and the accounts' opening balances tally.", data=data)
        if not _differs(o["accounts_net"], -o["stock_value"], MONEY_STEP):
            return Outcome("PASS", f"{have}; the accounts' opening balances tally together with the opening stock "
                                   f"value of {_amt(o['stock_value'])}.", data=data)
        stock = (f", and the opening stock is valued at {_amt(o['stock_value'])}"
                 if _differs(o["stock_value"], 0.0, MONEY_STEP) else "")
        warning = self.tb.get("warning") or ("Usual causes: opening balances were not carried forward into this FY "
                                             "database, or an opening-stock difference.")
        return Outcome("WARN", f"{have}, but they don't tally: the accounts' opening balances add up to "
                               f"{drcr(o['accounts_net'])}{stock}.", [warning], data)

    def info_vouchers(self) -> Outcome:
        v = self.overview["vouchers"]
        n = int(v.get("n") or 0)
        if not n:
            return Outcome("INFO", f"none yet in {self.fy}.", data={"count": 0})
        first, last = str(v.get("first_date"))[:10], str(v.get("last_date"))[:10]
        message = f"{n:,} counted, dated {_day(first)} to {_day(last)}."
        if first < self.start.isoformat() or last > self.end.isoformat():
            message += f" Some are dated outside {self.fy}."
        return Outcome("INFO", message, data={"count": n, "first_date": first, "last_date": last})

    def info_cancelled(self) -> Outcome:
        db = self.db
        cols = db.existing_columns("Tran1", ["Cancelled", "VchCancelled"])
        if not cols:
            return Outcome("INFO", "this BUSY version has no cancelled-voucher flag, so none are left out.")
        if db.settings.include_cancelled:
            n = int(db.query(f"SELECT COUNT(*) AS n FROM Tran1 WHERE {' OR '.join(f'{c} <> 0' for c in cols)}")[0]["n"]
                    or 0)
            return Outcome("WARN", f"{n:,} {'is' if n == 1 else 'are'} being COUNTED because the setting "
                                   "BUSY_INCLUDE_CANCELLED is on, so numbers can differ from BUSY. Turn it off unless "
                                   "you need it.", data={"count": n, "excluded": False})
        n = len(cancelled_vouchers(db))
        return Outcome("INFO", f"{n:,}, left out of every report." if n else "none.",
                       data={"count": n, "excluded": True})

    def info_masters(self) -> Outcome:
        counts = self.overview["masters"]
        return Outcome("INFO", ", ".join(f"{k}: {v:,}" for k, v in counts.items()) + ".", data=dict(counts))

    # ------------------------------------------------------------------ five numbers

    def run_numbers(self) -> list[Number]:
        plan: list[tuple[int, str, str, Callable[[Number], Number]]] = [
            (1, "trial_balance", "Trial balance totals", self.number_trial_balance),
            (2, "party_balance", "Closing balance of a customer", self.number_party_balance),
            (3, "sales_by_month", "Sales per month", self.number_sales),
            (4, "item_closing_stock", "Closing stock of an item", self.number_item_stock),
            (5, "pending_bills", "Pending bills of a party", self.number_bills),
        ]
        out = []
        for number, key, title, work in plan:
            n = Number(number, key, title)
            try:
                n = work(n)
            except BusyConnectionError:
                raise
            except ReportError as exc:
                n = self._not_available(Number(number, key, title), str(exc))
            except Exception as exc:  # noqa: BLE001 - one broken number must not hide the others
                n = Number(number, key, title, "FAIL",
                           note=f"could not be worked out ({type(exc).__name__}: {exc}). Please report it.")
            out.append(n)
        return out

    def _not_available(self, n: Number, note: str) -> Number:
        n.status, n.note = "INFO", note
        return n

    def _cannot_use(self, n: Number, option: str, given: str, exc: ReportError) -> Number:
        reason = str(exc).replace(" Try search-masters.", "").replace(" Use the exact name or Code.", "")
        if self.options.redact:
            given, reason = "the name you gave", "no single match"
        else:
            given = f'"{given}"'
        n.status = "FAIL"
        n.note = (f"could not use {option} {given}: {reason.rstrip('.')}. Type the name exactly as BUSY shows it "
                  "(its alias works too).")
        return n

    def number_trial_balance(self, n: Number) -> Number:
        totals, on = self.tb["totals"], _day(self.end)
        groups = [(g["primary_group"], g["closing"]) for g in self.tb["by_primary_group"]]
        width = max((len(g) for g, _ in groups), default=0)
        n.title = f"Trial balance on {on}"
        n.where_in_busy = f"Trial Balance (Display menu), as on {on}"
        n.must_match = "total debit = total credit, and the difference is 0.00"
        n.lines = [f"total debit {_amt(totals['closing_dr'])}, total credit {_amt(totals['closing_cr'])}, "
                   f"difference {totals['net_difference']}"]
        if groups:
            n.lines += ["by main group:", *(f"  {g:<{width}}  {c:>18}" for g, c in groups)]
        n.values = {"as_on": self.end.isoformat(), "total_debit": totals["closing_dr"],
                    "total_credit": totals["closing_cr"], "difference": totals["net_difference"],
                    "main_groups": [{"group": g, "closing": c} for g, c in groups]}
        o = self.opening
        if (totals["net_difference"] != "0.00" and _differs(o["stock_value"], 0.0, MONEY_STEP)
                and not _differs(o["accounts_net"], -o["stock_value"], MONEY_STEP)):
            n.note = "busy-mcp's difference equals your opening stock value; see the trial balance check above."
        return n

    def number_party_balance(self, n: Number) -> Number:
        if self.options.party:
            try:
                m = find_master(self.db, self.options.party, (MT_LEDGER,))
            except ReportError as exc:
                return self._cannot_use(n, "--party", self.options.party, exc)
            chosen = "the account you chose"
        else:
            top = reports.outstanding(self.db, "receivable", None, aging=False, limit=1)["parties"]
            if not top:
                return self._not_available(n, f"no customer has a balance on {_day(self.end)}. Run again with "
                                              '--party "NAME" to pick an account.')
            m = find_master(self.db, top[0]["party"], (MT_LEDGER,))
            chosen = "the customer with the largest balance"
        st = reports.ledger_statement(self.db, str(m.code), None, None, limit=1)
        name = self.account(m.code)
        n.title = f"Closing balance of {name} ({chosen})"
        n.where_in_busy = f"the ledger of that account, {_day(self.start)} to {_day(self.end)}"
        n.must_match = "opening and closing balance"
        n.lines = [f"opening balance {st['opening_balance']}",
                   f"total debit {_amt(st['total_debit'])}, total credit {_amt(st['total_credit'])}",
                   f"closing balance {st['closing_balance']}"]
        n.values = {"party": name, "chosen": chosen, "from": st["from"], "to": st["to"],
                    "opening": st["opening_balance"], "total_debit": st["total_debit"],
                    "total_credit": st["total_credit"], "closing": st["closing_balance"]}
        return n

    def number_sales(self, n: Number) -> Number:
        sales = reports.trade_analysis(self.db, "sales", "month", None, None, return_types=[])
        returns = reports.trade_analysis(self.db, "sales", "month", None, None, vch_types=[VT_SALES_RETURN],
                                         return_types=[])
        by_month = {x["month"]: x for x in sales["lines"]}
        returned = {x["month"]: x for x in returns["lines"]}
        months: list[dict[str, Any]] = []
        for ym in _months(self.start, self.end):
            s, r = by_month.get(ym, {}), returned.get(ym, {})
            months.append({"month": ym, "invoices": int(s.get("invoices") or 0),
                           "sales_incl_tax": r2(s.get("gross_incl_tax")),
                           "sales_before_tax": r2(s.get("net_taxable_value")),
                           "returns_incl_tax": r2(r.get("gross_incl_tax"))})
        total = {k: r2(sum(m[k] for m in months)) for k in ("sales_incl_tax", "sales_before_tax", "returns_incl_tax")}
        total["invoices"] = sum(m["invoices"] for m in months)
        n.title = f"Sales per month, {self.fy}"
        n.where_in_busy = "Sales Register / monthly summary"
        n.must_match = "each month's total"
        n.lines = [f"{'Month':<9}{'Invoices':>9}{'Sales incl. tax':>18}{'Sales before tax':>18}"
                   f"{'Returns incl. tax':>19}"]
        n.lines += [f"{_month(m['month']):<9}{m['invoices']:>9,}{_amt(m['sales_incl_tax']):>18}"
                    f"{_amt(m['sales_before_tax']):>18}{_amt(m['returns_incl_tax']):>19}" for m in months]
        n.lines.append(f"{'Total':<9}{total['invoices']:>9,}{_amt(total['sales_incl_tax']):>18}"
                       f"{_amt(total['sales_before_tax']):>18}{_amt(total['returns_incl_tax']):>19}")
        n.values = {"months": months, "total": total}
        n.note = "Sales returns are shown separately; they are not taken off the sales figures."
        return n

    def number_item_stock(self, n: Number) -> Number:
        if self.options.item:
            try:
                m = find_master(self.db, self.options.item, (MT_ITEM,))
            except ReportError as exc:
                return self._cannot_use(n, "--item", self.options.item, exc)
            chosen = "the item you chose"
        else:
            ranked = self.busiest_items()
            if not ranked:
                return self._not_available(n, f"no item has stock movements in {self.fy}. Run again with "
                                              '--item "NAME" to pick one.')
            m = self.masters[ranked[0]]
            chosen = "the item with the most movement"
        row = self.stock_rows.get(m.name) if self.item_name_count[m.name] == 1 else None
        if row is None:  # another item has the same name: read this one by its code
            mv = reports.stock_item_movement(self.db, str(m.code), None, None, limit=1)
            row = {"opening_qty": mv["opening_qty"], "inward_qty": None, "outward_qty": None,
                   "closing_qty": mv["closing_qty"]}
        unit = self.unit(m.code)
        name = self.item(m.code)
        n.title = f"Closing stock of {name} ({chosen})"
        n.where_in_busy = f"Stock Status for that item, as on {_day(self.end)}"
        n.must_match = "closing quantity"
        figures = [f"opening {_qty(row['opening_qty'])}"]
        if row["inward_qty"] is not None:
            figures += [f"inward {_qty(row['inward_qty'])}", f"outward {_qty(row['outward_qty'])}"]
        figures.append(f"closing {_qty(row['closing_qty'])}{' ' + unit if unit else ''}")
        n.lines = [", ".join(figures)]
        n.values = {"item": name, "chosen": chosen, "unit": unit or None, "as_on": self.end.isoformat(),
                    **{k: row[k] for k in ("opening_qty", "inward_qty", "outward_qty", "closing_qty")}}
        return n

    def number_bills(self, n: Number) -> Number:
        if self.options.party:
            try:
                m = find_master(self.db, self.options.party, (MT_LEDGER,))
            except ReportError as exc:
                return self._cannot_use(n, "--party", self.options.party, exc)
            nature = "payable" if m.code in self.creditors else "receivable"
            out = reports.bills_outstanding(self.db, nature, party=str(m.code), limit=ALL)
            party = next(iter(out["parties"]), None)
            if not party or not party["bills"]:
                why = " (bill-by-bill details are not kept for it)" if out.get("note") else ""
                return self._not_available(n, f"{self.account(m.code)} has no pending bills on {_day(self.end)}{why}.")
            name, chosen = self.account(m.code), "the account you chose"
        else:
            reasons = []
            for nature in ("receivable", "payable"):
                out_or_reason = self.bills(nature)
                if isinstance(out_or_reason, str):
                    reasons.append(out_or_reason)
                    continue
                if out_or_reason["parties"]:
                    party = max(out_or_reason["parties"], key=lambda p: (p["bill_count"], p["pending_bills_total"]))
                    break
            else:
                why = f" ({reasons[0].rstrip('.')})" if reasons else ""
                return self._not_available(n, f"no party has pending bills on {_day(self.end)}{why}. Run again with "
                                              '--party "NAME" to pick an account.')
            name = self.name("Party", party["party"])
            chosen = f"the {'customer' if nature == 'receivable' else 'supplier'} with the most pending bills"
        lines = []
        for b in party["bills"][:BILLS_SHOWN]:
            text = f"bill {b['bill_no'] or '(no number)'}"
            if b["date"]:
                text += f" of {_day(b['date'])}"
            if b["due_date"]:
                text += f", due {_day(b['due_date'])}"
            text += f": {_amt(b['pending'])} pending"
            if b["overdue_days"]:
                text += f" ({b['overdue_days']:,} days overdue)"
            lines.append(text)
        if party["bill_count"] > len(lines):
            lines.append(f"... and {_n(party['bill_count'] - len(lines), 'more bill')}")
        lines.append(f"total pending {_amt(party['pending_bills_total'])} in {_n(party['bill_count'], 'bill')}; "
                     f"ledger balance {_amt(party['ledger_balance'])}")
        n.title = f"Pending bills of {name} ({chosen})"
        n.where_in_busy = f"Outstanding / bill-wise report, as on {_day(self.end)}"
        n.must_match = "pending amount of each bill"
        n.lines = lines
        n.values = {"party": name, "chosen": chosen, "nature": nature, "as_on": self.end.isoformat(),
                    "bill_count": party["bill_count"], "total_pending": party["pending_bills_total"],
                    "ledger_balance": party["ledger_balance"],
                    "bills": [{k: b[k] for k in ("bill_no", "date", "due_date", "pending", "overdue_days")}
                              for b in party["bills"]]}
        return n

    # ------------------------------------------------------------------------ report

    def meta(self) -> dict[str, Any]:
        target, name = self.db.target, reports.company_info(self.db).get("name")
        return {"tool": "busy-mcp verify", "version": __version__,
                "run_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "company": {"code": target.company, "name": self.name("Company", name) or None,
                            "financial_year": self.fy, "from": self.start.isoformat(), "to": self.end.isoformat()},
                "redacted": self.options.redact}

    def report(self, meta: dict[str, Any] | None = None,
               on_check: Callable[[Check], None] | None = None) -> dict[str, Any]:
        """Every check and the five numbers as plain JSON-ready data. `on_check` sees each check as it ends."""
        out = dict(meta or self.meta())
        checks = []
        for check in self.run_checks():
            checks.append(check)
            if on_check:
                on_check(check)
        numbers = self.run_numbers()
        statuses = {c.status for c in checks} | {n.status for n in numbers}
        result = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
        out.update(result=result, exit_code=1 if result == "FAIL" else 0, checks=[asdict(c) for c in checks],
                   compare=[asdict(n) for n in numbers])
        return out


def build_report(db: BusyDB, options: Options | None = None, targets: list[Target] | None = None) -> dict[str, Any]:
    """Run every check and work out the five numbers, without printing anything."""
    return Verifier(db, options, targets).report()


# ----------------------------------------------------------------------------- text output

def _result_line(result: str) -> str:
    if result == "FAIL":
        return ("Result: problems found. Don't rely on busy-mcp's answers in the areas marked [FAIL] until they are "
                "fixed or explained.")
    if result == "WARN":
        return "Result: no errors. Read the [WARN] lines, then compare the five numbers with BUSY."
    return "Result: every check passed. Now compare the five numbers with BUSY."


def _intro_lines(report: dict[str, Any]) -> list[str]:
    c = report["company"]
    who = f"{c['name']} ({c['code']})" if c.get("name") else str(c.get("code") or "company")
    run_at = _dt.datetime.fromisoformat(report["run_at"])
    return [f"busy-mcp verify {report['version']}, run on {_day(run_at)} at {run_at:%H:%M}",
            f"Company: {who}, financial year {c['financial_year']} ({_day(c['from'])} to {_day(c['to'])})",
            "busy-mcp only reads your BUSY data; nothing is changed.",
            "", "PART 1. Automatic checks on your data"]


def _wrap(text: str, first: str, rest: str) -> list[str]:
    """Wrap prose for a console about 100 characters wide (tables are never wrapped)."""
    return textwrap.wrap(text, WIDTH, initial_indent=first, subsequent_indent=rest, break_on_hyphens=False)


def _check_lines(check: dict[str, Any]) -> list[str]:
    lines = _wrap(f"{check['title']}: {check['message']}", f"[{check['status']:^4}] ", " " * 7)
    for detail in check["details"]:
        lines += _wrap(detail, "       - ", " " * 9)
    return lines


def _number_lines(n: dict[str, Any]) -> list[str]:
    out = ["", f"{n['number']}. {n['title']}"]
    if n["status"] != "OK":
        return out + _wrap(n["note"], f"   [{n['status']:^4}] ", " " * 10)
    out.append(f"   In BUSY:    {n['where_in_busy']}")
    out += [("   busy-mcp:   " if i == 0 else " " * 15) + line for i, line in enumerate(n["lines"])]
    out.append(f"   Must match: {n['must_match']}")
    if n["note"]:
        out += _wrap(n["note"], "   Note:       ", " " * 15)
    return out


def _tail_lines(report: dict[str, Any]) -> list[str]:
    lines = ["", "PART 2. Compare these five numbers with BUSY",
             "Open the same company and financial year in BUSY and look each number up where shown."]
    for n in report["compare"]:
        lines += _number_lines(n)
    lines += ["", *_wrap(_result_line(report["result"]), "", "")]
    if not report["redacted"]:
        lines.append("To share this report without names: busy-mcp verify --redact --out verify.txt")
    return lines


def render_text(report: dict[str, Any]) -> str:
    """The report exactly as `busy-mcp verify` prints it."""
    lines = _intro_lines(report)
    for check in report["checks"]:
        lines += _check_lines(check)
    return "\n".join(lines + _tail_lines(report))


# ----------------------------------------------------------------------------- command


def _print(text: str) -> None:
    """print() that survives a console unable to show a character (a name in Hindi, say)."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding))


def _save(path: str | None, text: str, as_json: bool, out: Callable[[str], Any]) -> bool:
    if not path:
        return True
    try:
        Path(path).write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Could not save the report to {path}: {exc}", file=sys.stderr)
        return False
    if not as_json:
        out(f"Report saved to {path}")
    return True


def _stop(message: str, args: argparse.Namespace, out: Callable[[str], Any]) -> int:
    """Report a problem that prevents any check from running."""
    if args.json:
        text = json.dumps({"tool": "busy-mcp verify", "version": __version__, "result": "FAIL", "exit_code": 1,
                           "error": message, "checks": [], "compare": []}, indent=2)
        out(text)
    else:
        lines = [f"busy-mcp verify {__version__}", f"[FAIL] {message}", "Run 'busy-mcp doctor' for a full diagnosis."]
        for line in lines:
            out(line)
        text = "\n".join(lines)
    _save(args.out, text, args.json, out)
    return 1


def run_verify(args: argparse.Namespace, out: Callable[[str], Any] = _print) -> int:
    """``busy-mcp verify``: 0 when no check failed, 1 otherwise."""
    as_json = bool(args.json)
    options = Options(party=args.party, item=args.item, redact=bool(args.redact))
    try:
        settings = load_settings()
        targets = discover_targets(settings)
        if args.company or args.fy:
            target = choose_target(targets, args.company or settings.company, args.fy)
        else:
            target = choose_target(targets, settings.company, settings.fy)
    except BusyMCPError as exc:
        return _stop(f"Cannot find your BUSY data: {exc}", args, out)

    def show(check: Check) -> None:
        for line in _check_lines(asdict(check)):
            out(line)

    try:
        with BusyDB(target, settings) as db:
            verifier = Verifier(db, options, targets)
            meta = verifier.meta()
            if not as_json:
                for line in _intro_lines(meta):
                    out(line)
            report = verifier.report(meta, None if as_json else show)
    except BusyMCPError as exc:
        return _stop(f"Cannot read {target.key}: {exc}", args, out)
    except Exception as exc:  # noqa: BLE001 - like doctor: report in plain words, don't crash
        return _stop(f"Cannot read {target.key}: {type(exc).__name__}: {exc}", args, out)
    if as_json:
        text = json.dumps(report, indent=2)
        out(text)
    else:
        for line in _tail_lines(report):
            out(line)
        text = render_text(report)
    saved = _save(args.out, text, as_json, out)
    return int(report["exit_code"]) if saved else 1
