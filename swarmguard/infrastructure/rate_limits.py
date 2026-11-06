"""Token-bucket rate limiting keyed by *trusted* identity (never by a client-supplied header).

Keys are bounded (oldest buckets evicted) so an attacker cycling identifiers cannot exhaust memory.
Limits are per process; a multi-replica deployment would need a shared limiter.
"""
from __future__ import annotations
import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class RateLimiter:
    def __init__(self, *, capacity: int, refill_per_second: float, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("invalid rate limit")
        self.capacity, self.refill = capacity, refill_per_second
        self._max_keys, self._clock = max_keys, clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            allowed = tokens >= cost
            self._buckets[key] = (tokens - cost if allowed else tokens, now)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return allowed

    def retry_after(self, key: str) -> float:
        with self._lock:
            tokens, _ = self._buckets.get(key, (float(self.capacity), 0.0))
        return max(0.0, (1 - tokens) / self.refill)
