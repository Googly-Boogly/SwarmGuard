"""Injectable clocks.

Every trusted component reads time through a ``Clock`` so that tests and the
demonstration are reproducible (``SteppingClock``) and expiry can be simulated
(``ManualClock.advance``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Deterministic clock. Optionally advances by ``step`` after every read."""

    def __init__(
        self,
        start: datetime | None = None,
        step: timedelta = timedelta(0),
    ) -> None:
        self._current = start or datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        if self._current.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware start time")
        self._step = step

    def now(self) -> datetime:
        current = self._current
        self._current = current + self._step
        return current

    def advance(self, delta: timedelta) -> None:
        self._current += delta


def SteppingClock(start: datetime | None = None) -> ManualClock:  # noqa: N802
    """A clock that advances one second per read: reproducible, ordered timestamps."""
    return ManualClock(start=start, step=timedelta(seconds=1))
