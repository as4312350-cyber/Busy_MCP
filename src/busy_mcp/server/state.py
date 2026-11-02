"""Process-wide state: discovered targets, the selected one, open connections."""

from __future__ import annotations

import logging
import threading

from ..config import Settings
from ..db import BusyDB, Target, choose_target, discover_targets

log = logging.getLogger("busy_mcp.state")


class State:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._targets: list[Target] | None = None
        self._dbs: dict[str, BusyDB] = {}
        self.active: Target | None = None
        self.lock = threading.RLock()

    def targets(self, refresh: bool = False) -> list[Target]:
        with self.lock:
            if self._targets is None or refresh:
                self._targets = discover_targets(self.settings)
            return self._targets

    def db(self, company: str | None = None, fy: int | None = None) -> BusyDB:
        """The database for a company/year (default: the selected one), with
        caches refreshed if BUSY data changed since the last check."""
        with self.lock:
            if company or fy:
                base = company or (self.active.company if self.active else self.settings.company)
                target = choose_target(self.targets(), base, fy)
            else:
                if self.active is None:
                    self.active = choose_target(self.targets(), self.settings.company, self.settings.fy)
                target = self.active
            db = self._dbs.get(target.key)
            if db is None:
                db = self._dbs[target.key] = BusyDB(target, self.settings)
        db.ensure_fresh()
        return db

    def select(self, company: str | None, fy: int | None) -> Target:
        with self.lock:
            self.active = choose_target(self.targets(refresh=True), company or self.settings.company, fy)
            return self.active

    def prefetch(self) -> None:
        """Warm the caches of the default company so the first question is fast.
        Runs in a background thread; failures are logged, never raised."""
        def run() -> None:
            try:
                from ..reports import item_index, ledger_index

                db = self.db()
                db.masters()
                ledger_index(db)
                item_index(db)
                log.info("prefetch done for %s: %s", db.target.key, db.cache_info())
            except Exception as exc:  # noqa: BLE001 - prefetch is best effort
                log.warning("prefetch skipped: %s", exc)

        threading.Thread(target=run, name="busy-mcp-prefetch", daemon=True).start()

    def close(self) -> None:
        with self.lock:
            for d in self._dbs.values():
                d.close()
            self._dbs.clear()
