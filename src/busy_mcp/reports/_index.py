"""In-memory movement index.

One aggregate query per data version turns ``Tran2`` into per-master daily
totals with prefix sums. After that, the movement of any master over any date
range is two binary searches, so trial balance, P&L, balance sheet, group
summaries, aging and stock summaries for any period need no further database
round trips. The index is rebuilt only when BUSY data changes (see
``BusyDB.ensure_fresh``).
"""

from __future__ import annotations

import datetime as _dt
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Series:
    days: list[int] = field(default_factory=list)  # date ordinals, ascending, unique
    prefix: list[list[float]] = field(default_factory=list)  # prefix[k] = sums of the first k days


class MovementIndex:
    """Per-code prefix sums of `width` metrics keyed by day."""

    def __init__(self, width: int, rows: Iterable[tuple[int, _dt.date, Sequence[float]]]):
        self.width = width
        staged: dict[int, dict[int, list[float]]] = {}
        for code, day, metrics in rows:
            per_day = staged.setdefault(code, {})
            slot = per_day.setdefault(day.toordinal(), [0.0] * width)
            for i, v in enumerate(metrics):
                slot[i] += v
        self._series: dict[int, _Series] = {}
        self.row_count = 0
        for code, per_day in staged.items():
            s = _Series()
            running = [0.0] * width
            s.prefix.append(list(running))
            for ordinal in sorted(per_day):
                running = [a + b for a, b in zip(running, per_day[ordinal], strict=True)]
                s.days.append(ordinal)
                s.prefix.append(running)
            self._series[code] = s
            self.row_count += len(s.days)

    def codes(self) -> Iterable[int]:
        return self._series.keys()

    def range(self, code: int, start: _dt.date | None, end: _dt.date) -> tuple[float, ...]:
        """Sum of each metric for start..end inclusive (start=None: from the beginning)."""
        s = self._series.get(code)
        if s is None:
            return (0.0,) * self.width
        i = 0 if start is None else bisect_left(s.days, start.toordinal())
        j = bisect_right(s.days, end.toordinal())
        if j <= i:
            return (0.0,) * self.width
        lo, hi = s.prefix[i], s.prefix[j]
        return tuple(h - low for h, low in zip(hi, lo, strict=True))

    def all_ranges(self, start: _dt.date | None, end: _dt.date) -> dict[int, tuple[float, ...]]:
        out = {}
        for code in self._series:
            r = self.range(code, start, end)
            if any(abs(x) > 1e-9 for x in r):
                out[code] = r
        return out

    def monthly(self, codes: Iterable[int], start: _dt.date, end: _dt.date) -> dict[str, list[float]]:
        """Per calendar month (YYYY-MM) totals over `codes` within start..end."""
        months: dict[str, list[float]] = {}
        cur = _dt.date(start.year, start.month, 1)
        while cur <= end:
            nxt = _dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            lo = max(cur, start)
            hi = min(nxt - _dt.timedelta(days=1), end)
            totals = [0.0] * self.width
            for code in codes:
                for k, v in enumerate(self.range(code, lo, hi)):
                    totals[k] += v
            months[cur.strftime("%Y-%m")] = totals
            cur = nxt
        return months

    def stats(self) -> dict[str, Any]:
        return {"codes": len(self._series), "daily_rows": self.row_count}
