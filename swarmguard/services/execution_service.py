"""Protected execution service (trusted). Reachable only on the internal gateway<->executor network.

``POST /internal/v1/execute`` accepts nothing but a gateway-signed grant; the operation, action and
parameters are read from the trusted database. No dynamic tool names, no code execution, no networking
in tools. Liveness/readiness expose check names only.
"""
from __future__ import annotations
import contextlib
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from swarmguard.authentication.keys import load_public_key
from swarmguard.clock import Clock, SystemClock
from swarmguard.execution.executor import ProtectedExecutor
from swarmguard.infrastructure.health import liveness, readiness
from swarmguard.infrastructure.metrics import ServiceMetrics
from swarmguard.services.common import heartbeat_task, install_request_limits
from swarmguard.services.settings import CommonSettings, _env
from swarmguard.storage.database import Database

SERVICE = "executor"


def create_execution_app(executor: ProtectedExecutor, *, heartbeat_seconds: float = 5.0) -> FastAPI:
    metrics = ServiceMetrics(SERVICE)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.recovery = executor.recover()
        async with heartbeat_task(executor.db, executor.clock, SERVICE, heartbeat_seconds, metrics.snapshot):
            yield

    app = FastAPI(title="SwarmGuard protected executor", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    install_request_limits(app, 4096)

    @app.get("/health/live")
    async def live() -> dict[str, Any]:
        return liveness(SERVICE)

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        async def db_ok() -> bool:
            return executor.db.ping() and not executor.db.pending_migrations()
        status, body = await readiness(SERVICE, {"database": db_ok})
        return JSONResponse(body, status_code=status)

    @app.post("/internal/v1/execute")
    async def execute(request: Request) -> JSONResponse:
        header = request.headers.get("authorization", "")
        token = header[len("Bearer "):] if header.startswith("Bearer ") else None
        reply, operation_id = await run_in_threadpool(executor.execute, token)
        metrics.inc(f"execute.{reply.state or 'REJECTED'}")
        return JSONResponse(reply.body(operation_id), status_code=reply.http_status)

    return app


def app_from_env(clock: Clock | None = None) -> FastAPI:
    common = CommonSettings.from_env()
    clock = clock or SystemClock()
    db = Database(common.database_url, migrate=common.migrate)
    executor = ProtectedExecutor(db=db, clock=clock, grant_public_key=load_public_key(_env("SWARMGUARD_GRANT_PUBLIC_KEY_FILE")),
                                 approval_signing_key=common.approval_key, approver_ids=list(common.approvers))
    return create_execution_app(executor, heartbeat_seconds=common.heartbeat_seconds)

