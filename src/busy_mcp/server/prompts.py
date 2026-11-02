"""Reusable prompts that chain the tools into common accountant workflows."""

from __future__ import annotations

from typing import Any


def register_prompts(mcp: Any) -> None:
    @mcp.prompt(name="financial-health-check", description="Review the books of the selected year end to end.")
    def financial_health_check(as_of: str = "") -> str:
        when = f" as on {as_of}" if as_of else " for the selected financial year"
        return (
            f"Do a financial health check of the selected BUSY company{when}.\n"
            "1. dashboard for the headline numbers.\n"
            "2. trial-balance (ledger_level=false) - confirm it tallies; explain any warning.\n"
            "3. profit-and-loss and balance-sheet - margins, biggest heads, unusual balances.\n"
            "4. bills-outstanding (or outstanding) - customers overdue more than 90 days.\n"
            "5. stock-summary only_negative=true and batch-stock expiring_within_days=60.\n"
            "6. gst-summary - net GST payable.\n"
            "Report a short prioritised list with numbers, and repeat every caveat/warning the tools return."
        )

    @mcp.prompt(name="receivables-follow-up", description="Prioritised list of customers to chase, with contacts.")
    def receivables_follow_up(min_overdue_days: str = "30") -> str:
        return (
            "Use bills-outstanding (nature=receivable, overdue_only=true); if the company has no bill-wise data, "
            "use outstanding instead. Keep parties whose oldest overdue bill is older than "
            f"{min_overdue_days} days, largest first. For the top 10 call master-details for phone/e-mail and "
            "ledger-statement for the last receipt. Produce a table: party, overdue amount, oldest days, "
            "last receipt date, phone, e-mail, and a one-line suggested action."
        )

    @mcp.prompt(name="month-end-review", description="Month-end review of sales, purchases, cash and taxes.")
    def month_end_review(month: str = "") -> str:
        scope = f"the month {month} (YYYY-MM)" if month else "the latest complete month"
        return (
            f"Review {scope}: dashboard for the month and the previous month, sales-purchase-analysis "
            "(group_by=party) for both, cash-bank-summary, gst-summary, and day-book for vouchers above the "
            "usual size. Highlight changes over 20% and anything that needs action before closing the month."
        )

    @mcp.prompt(name="stock-review", description="Stock health: negative stock, slow movers, expiry, godowns.")
    def stock_review() -> str:
        return (
            "Review inventory: stock-summary (only_negative=true), stock-summary sorted by closing quantity with "
            "no outward movement this year (slow movers), batch-stock expiring_within_days=90, and "
            "stock-by-location totals. List concrete actions."
        )

    @mcp.prompt(name="audit-check", description="Look for risky changes: deletions, back-dated edits, odd users.")
    def audit_check(from_date: str = "", to_date: str = "") -> str:
        rng = f" from {from_date} to {to_date}" if from_date or to_date else " for the selected year"
        return (
            f"Run audit-trail{rng}. Flag deleted vouchers, vouchers modified long after their date, and unusual "
            "activity by user; open voucher-details for the five most significant cases."
        )
