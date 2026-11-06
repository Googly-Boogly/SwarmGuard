"""Shared service middleware: request-size limit, correlation IDs, heartbeats."""
from __future__ import annotations
import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from swarmguard.clock import Clock
from swarmguard.storage.database import DATABASE_ERRORS, Database
from swarmguard.storage.repositories import HeartbeatRepository

logger = logging.getLogger("swarmguard.services")


def install_request_limits(app: FastAPI, max_body_bytes: int) -> None:
    @app.middleware("http")
    async def limits(request: Request, call_next: Callable[..., Any]) -> Any:
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > max_body_bytes):
            return JSONResponse({"detail": "request body too large", "reason_code": "INVALID_ACTION"}, status_code=413)
        body = await request.body()
        if len(body) > max_body_bytes:
            return JSONResponse({"detail": "request body too large", "reason_code": "INVALID_ACTION"}, status_code=413)
        correlation = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = correlation if correlation.isalnum() and len(correlation) <= 64 else \
            secrets.token_hex(8)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def heartbeat_once(db: Database, clock: Clock, service: str, detail: dict[str, Any]) -> None:
    try:
        HeartbeatRepository(db).beat(service, "UP", detail, clock.now().isoformat())
    except DATABASE_ERRORS:
        logger.warning("heartbeat for %s failed (database unavailable)", service)


@contextlib.asynccontextmanager
async def heartbeat_task(db: Database, clock: Clock, service: str, interval: float,
                         detail: Callable[[], dict[str, Any]]) -> Any:
    """Heartbeats use their own connection in a worker thread so a locked or slow database can never
    stall request handling (which must fail closed promptly)."""
    beat_db = Database(db.path, migrate=False) if interval > 0 else db
    await asyncio.to_thread(heartbeat_once, beat_db, clock, service, detail())
    task = None
    if interval > 0:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval)
                await asyncio.to_thread(heartbeat_once, beat_db, clock, service, detail())
        task = asyncio.create_task(loop())
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if beat_db is not db:
            beat_db.close()
