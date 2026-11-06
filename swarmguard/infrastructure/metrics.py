"""Low-cardinality in-process operational counters. Labels are fixed names or bounded enums only.

Proposed violations (blocked requests) and executed violations (prohibited outcomes) are separate
metrics. Never put secrets, tokens, recipients or free text into metric names.
"""
from __future__ import annotations
import threading
import time
from collections import Counter, deque
from statistics import median
from typing import Any


class ServiceMetrics:
    def __init__(self, service: str) -> None:
        self.service = service
        self.started = time.monotonic()
        self._counters: Counter[str] = Counter()
        self._latency_ms: deque[float] = deque(maxlen=2000)
        self._lock = threading.Lock()
        self.in_flight = 0

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_latency(self, ms: float) -> None:
        with self._lock:
            self._latency_ms.append(ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = list(self._latency_ms)
            counters = dict(self._counters)
        return {"service": self.service, "uptime_seconds": round(time.monotonic() - self.started, 1),
                "counters": counters, "in_flight": self.in_flight,
                "latency_ms": {"n": len(samples), "mean": (sum(samples) / len(samples)) if samples else None,
                               "median": median(samples) if samples else None}}
