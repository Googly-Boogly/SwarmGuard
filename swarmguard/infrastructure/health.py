"""Liveness vs readiness. Readiness reports check names and pass/fail only (no configuration values)."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import Any

Check = Callable[[], Awaitable[bool]]


def liveness(service: str) -> dict[str, Any]:
    return {"service": service, "status": "alive"}


async def readiness(service: str, checks: dict[str, Check]) -> tuple[int, dict[str, Any]]:
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            results[name] = "ok" if await check() else "failed"
        except Exception:  # noqa: BLE001 - any failing dependency means not ready
            results[name] = "failed"
    ready = all(v == "ok" for v in results.values())
    return (200 if ready else 503), {"service": service, "status": "ready" if ready else "not_ready", "checks": results}
