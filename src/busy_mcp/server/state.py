"""Process-wide state: discovered targets, the selected one, open connections."""

from __future__ import annotations

import threading

from ..config import Settings
from ..db import BusyDB, Target, choose_target, discover_targets


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
        with self.lock:
            if company or fy:
                base = company or (self.active.company if self.active else self.settings.company)
                target = choose_target(self.targets(), base, fy)
            else:
                if self.active is None:
                    self.active = choose_target(self.targets(), self.settings.company, self.settings.fy)
                target = self.active
            if target.key not in self._dbs:
                self._dbs[target.key] = BusyDB(target, self.settings)
            return self._dbs[target.key]

    def select(self, company: str | None, fy: int | None) -> Target:
        with self.lock:
            self.active = choose_target(self.targets(refresh=True), company or self.settings.company, fy)
            return self.active

    def close(self) -> None:
        with self.lock:
            for d in self._dbs.values():
                d.close()
            self._dbs.clear()
