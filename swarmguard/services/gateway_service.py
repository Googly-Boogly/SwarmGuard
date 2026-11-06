"""Capability gateway service (trusted). The only service reachable from the untrusted agent network.

It owns: credential verification (public key only), the policy engine, approval validation, the
existing ``CapabilityGateway`` and the grant-signing key for the executor. It does not own: the identity
issuer key, operator credentials, or the executor. Its database role cannot write ``sent_emails``.
"""
from __future__ import annotations
import contextlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from swarmguard.agents.workspace import SharedWorkspace
from swarmguard.authentication.keys import load_private_key, load_public_key
from swarmguard.authentication.service_identity import ServiceIdentityVerifier, assert_disjoint_approvers
from swarmguard.clock import Clock, SystemClock
from swarmguard.execution.remote_backend import ExecutorChannel, RemoteExecutionBackend
from swarmguard.infrastructure.failure_recovery import recover_gateway
from swarmguard.infrastructure.health import liveness, readiness
from swarmguard.infrastructure.metrics import ServiceMetrics
from swarmguard.infrastructure.rate_limits import RateLimiter
from swarmguard.security.approval_manager import ApprovalManager
from swarmguard.security.capability_gateway import CapabilityGateway
from swarmguard.security.identity import IdentityRegistry
from swarmguard.security.policy_engine import PolicyEngine
from swarmguard.security.remote_gateway import RemoteCapabilityGateway, _Limits
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.services.common import heartbeat_task, install_request_limits
from swarmguard.services.settings import CommonSettings, _env
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ProtectedOperationRepository, Repositories

SERVICE = "gateway"


@dataclass
class GatewayConfig:
    database_url: str
    identity_public_key: Ed25519PublicKey
    grant_private_key: Ed25519PrivateKey = field(repr=False)
    approval_signing_key: bytes = field(repr=False)
    approver_ids: list[str]
    executor_channel: ExecutorChannel
    migrate: bool = False
    max_body_bytes: int = 65_536
    max_concurrent_operations: int = 8
    agent_requests_per_minute: int = 120
    executor_attempts: int = 3
    grant_ttl: timedelta = timedelta(seconds=30)
    heartbeat_seconds: float = 5.0
    max_workflow_proposals: int | None = 500


class GatewayPlane:
    """Trusted objects of the gateway process. Never exposed to agents."""

    def __init__(self, cfg: GatewayConfig, clock: Clock) -> None:
        self.clock = clock
        self.db = Database(cfg.database_url, migrate=cfg.migrate)
        self.repos = Repositories(self.db)
        self.monitor = WorkflowMonitor(self.db, clock, self.repos)
        self.workspace = SharedWorkspace(self.db, self.monitor, clock, self.repos)
        self.identities = IdentityRegistry()           # filled lazily from DB bindings by trusted code only
        self.approvals = ApprovalManager(self.db, self.monitor, clock, self.identities,
                                         signing_key=cfg.approval_signing_key, repos=self.repos)
        assert_disjoint_approvers(self.db, cfg.approver_ids)
        for approver in cfg.approver_ids:
            self.approvals.register_approver(approver)
        self.approvals.seal()
        self.backend = RemoteExecutionBackend(db=self.db, clock=clock, grant_key=cfg.grant_private_key,
                                              channel=cfg.executor_channel, max_attempts=cfg.executor_attempts,
                                              grant_ttl=cfg.grant_ttl)
        self.gateway = CapabilityGateway(db=self.db, identities=self.identities, policy_engine=PolicyEngine(),
                                         monitor=self.monitor, workspace=self.workspace, approvals=self.approvals,
                                         clock=clock, max_workflow_proposals=cfg.max_workflow_proposals,
                                         execution_backend=self.backend)
        self.metrics = ServiceMetrics(SERVICE)
        per_second = cfg.agent_requests_per_minute / 60
        self.remote = RemoteCapabilityGateway(
            db=self.db, clock=clock, gateway=self.gateway, identities=self.identities, monitor=self.monitor,
            verifier=ServiceIdentityVerifier(cfg.identity_public_key, self.db), metrics=self.metrics,
            limits=_Limits(agent=RateLimiter(capacity=max(1, cfg.agent_requests_per_minute // 2),
                                             refill_per_second=per_second),
                           max_concurrent=cfg.max_concurrent_operations))

    def heartbeat_detail(self) -> dict[str, Any]:
        snapshot = self.metrics.snapshot()
        try:
            snapshot["operations"] = ProtectedOperationRepository(self.db).counts()
        except Exception:  # noqa: BLE001 - observability is best-effort
            snapshot["operations"] = None
        return snapshot


def create_gateway_app(cfg: GatewayConfig, *, clock: Clock | None = None) -> FastAPI:
    plane = GatewayPlane(cfg, clock or SystemClock())

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.recovery = recover_gateway(ProtectedOperationRepository(plane.db), plane.monitor, plane.clock)
        async with heartbeat_task(plane.db, plane.clock, SERVICE, cfg.heartbeat_seconds, plane.heartbeat_detail):
            yield

    app = FastAPI(title="SwarmGuard capability gateway", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.plane = plane
    install_request_limits(app, cfg.max_body_bytes)

    @app.get("/health/live")
    async def live() -> dict[str, Any]:
        return liveness(SERVICE)

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        async def db_ok() -> bool:
            return plane.db.ping() and not plane.db.pending_migrations()

        async def executor_ok() -> bool:
            return await plane.backend.channel.live()
        status, body = await readiness(SERVICE, {"database": db_ok, "executor": executor_ok})
        return JSONResponse(body, status_code=status)

    @app.post("/api/v1/capabilities/execute")
    async def execute(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        peer = request.client.host if request.client else "unknown"   # socket peer, not a forwarded header
        result = await plane.remote.handle(request.headers.get("authorization"), payload, peer=peer)
        return JSONResponse(result.body, status_code=result.http_status)

    return app


def config_from_env(channel: ExecutorChannel) -> GatewayConfig:
    """Built by the ``swarmguard_services.gateway_main`` entry point, which injects the HTTP channel."""
    common = CommonSettings.from_env()
    return GatewayConfig(database_url=common.database_url, migrate=common.migrate,
                         identity_public_key=load_public_key(_env("SWARMGUARD_IDENTITY_PUBLIC_KEY_FILE")),
                         grant_private_key=load_private_key(_env("SWARMGUARD_GRANT_PRIVATE_KEY_FILE")),
                         approval_signing_key=common.approval_key, approver_ids=list(common.approvers),
                         executor_channel=channel, heartbeat_seconds=common.heartbeat_seconds)
