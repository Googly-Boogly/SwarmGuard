"""Agent scheduling strategies (sequential; no uncontrolled parallelism).

* ``DETERMINISTIC``: the first agent in the fixed priority order that is eligible.
* ``ROUND_ROBIN``: cycle through every active agent, eligible or not (idle WAITs are possible).
* ``DEPENDENCY_AWARE`` (default): only agents with work. The coordinator and the adversary are
  always candidates; other agents need an open task. Any agent whose permitted view has not
  changed since its last WAIT or rejected decision is skipped. Ties are broken round-robin in
  the fixed priority order.
"""

from __future__ import annotations

from enum import StrEnum


class SchedulingStrategy(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    ROUND_ROBIN = "ROUND_ROBIN"
    DEPENDENCY_AWARE = "DEPENDENCY_AWARE"


class Scheduler:
    def __init__(self, order: list[str], strategy: SchedulingStrategy = SchedulingStrategy.DEPENDENCY_AWARE) -> None:
        if len(set(order)) != len(order) or not order:
            raise ValueError("scheduler needs a non-empty, duplicate-free agent order")
        self.order = list(order)
        self.strategy = strategy
        self._last: str | None = None

    def next_agent(self, eligible: set[str], active: set[str]) -> str | None:
        if self.strategy is SchedulingStrategy.ROUND_ROBIN:
            pool = [a for a in self.order if a in active]
        else:
            pool = [a for a in self.order if a in eligible and a in active]
        if not pool:
            return None
        if self.strategy is SchedulingStrategy.DETERMINISTIC or self._last not in self.order:
            choice = pool[0]
        else:
            start = self.order.index(self._last) + 1
            rotated = self.order[start:] + self.order[:start]
            choice = next(a for a in rotated if a in pool)
        self._last = choice
        return choice
