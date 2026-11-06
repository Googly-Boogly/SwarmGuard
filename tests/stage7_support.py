"""Isolated Stage 7 topology for tests: separate DB connections per service, ASGI transports, real keys."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import httpx
from swarmguard.authentication.keys import generate_keypair, random_secret
from swarmguard.authentication.service_identity import CredentialIssuer
from swarmguard.clock import SystemClock
from swarmguard.execution.executor import ProtectedExecutor
from swarmguard.services.control_plane import ControlPlane
from swarmguard.services.execution_service import create_execution_app
from swarmguard.services.gateway_service import GatewayConfig, create_gateway_app
from swarmguard.storage.database import Database
from swarmguard_services.http_channel import HttpExecutorChannel
from cryptography.hazmat.primitives import serialization

WORKFLOW = "wf_remote"
APPROVER = "operator_alice"
EMAIL = {"recipient": "customer@example.test", "subject": "Service Update", "body": "Your request has been received."}


def _keys() -> tuple[Any, Any]:
    private_pem, public_pem = generate_keypair()
    return (serialization.load_pem_private_key(private_pem, None), serialization.load_pem_public_key(public_pem))


@dataclass
class Stage7Env:
    tmp: Path
    db_url: str | None = None
    approvers: list[str] = field(default_factory=lambda: [APPROVER])

    def __post_init__(self) -> None:
        self.db_url = self.db_url or str(self.tmp / "stage7.db")
        self.clock = SystemClock()
        self.identity_private, self.identity_public = _keys()
        self.grant_private, self.grant_public = _keys()
        self.approval_key = random_secret()
        self.control = ControlPlane(Database(self.db_url), self.clock, approval_signing_key=self.approval_key,
                                    approver_ids=self.approvers)
        self.control.seed_synthetic_records()
        self.control.create_workflow(WORKFLOW, "Remote customer communication",
                                     ["coordinator", "research_agent", "operations_agent"], by="admin_test")
        self.control.register_runtime("runtime_alpha", "test agent runtime", by="admin_test")
        self.control.register_runtime("runtime_beta", "second runtime", by="admin_test")
        self.control.bind_agent("operations_agent", "runtime_alpha", "operations", [WORKFLOW], None, by="admin_test")
        self.control.bind_agent("research_agent", "runtime_beta", "research", [WORKFLOW], None, by="admin_test")
        self.executor = ProtectedExecutor(db=Database(self.db_url, migrate=False), clock=self.clock,
                                          grant_public_key=self.grant_public, approval_signing_key=self.approval_key,
                                          approver_ids=self.approvers)
        self.executor_app = create_execution_app(self.executor, heartbeat_seconds=0)
        self.build_gateway()
        self.issuer = CredentialIssuer(self.identity_private)

    def build_gateway(self, **overrides: Any) -> None:
        cfg = GatewayConfig(database_url=self.db_url, identity_public_key=self.identity_public,
                            grant_private_key=self.grant_private, approval_signing_key=self.approval_key,
                            approver_ids=self.approvers,
                            executor_channel=HttpExecutorChannel("http://executor",
                                                                 transport=overrides.pop("executor_transport", None)
                                                                 or httpx.ASGITransport(app=self.executor_app)),
                            heartbeat_seconds=0,
                            executor_attempts=2, **overrides)
        self.gateway_app = create_gateway_app(cfg, clock=self.clock)
        self.plane = self.gateway_app.state.plane

    def token(self, agent: str = "operations_agent", runtime: str = "runtime_alpha", **kw: Any) -> str:
        return self.issuer.issue(agent_id=agent, runtime_id=runtime, **kw)[0]

    def agent(self, token: str | None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.gateway_app), base_url="http://gateway",
                                 headers=headers)

    async def call(self, token: str | None, **body: Any) -> httpx.Response:
        async with self.agent(token) as client:
            return await client.post("/api/v1/capabilities/execute", json=body)

    async def draft_and_request(self, token: str, prefix: str = "r") -> tuple[str, str]:
        draft = (await self.call(token, request_id=f"{prefix}_draft", workflow_id=WORKFLOW, tool="prepare_email",
                                 parameters=EMAIL)).json()
        assert draft["executed"], draft
        email_id = draft["execution_result"]["resource_id"]
        req = (await self.call(token, request_id=f"{prefix}_req", workflow_id=WORKFLOW, tool="request_approval",
                               resource_id=email_id, resource_version=1, parameters={"operation": "send_email"})).json()
        assert req["executed"], req
        return email_id, req["execution_result"]["approval_id"]

    def approve(self, approval_id: str) -> None:
        packet = self.control.approvals.review_packet(approval_id)
        self.control.grant_approval(approval_id, approver_id=APPROVER, operation_hash=packet.operation_hash)

    def send_body(self, request_id: str, email_id: str, approval_id: str | None, **extra: Any) -> dict[str, Any]:
        return {"request_id": request_id, "workflow_id": WORKFLOW, "tool": "send_email", "resource_id": email_id,
                "resource_version": 1, "parameters": {**EMAIL, **extra}, **({"approval_id": approval_id} if approval_id else {})}

    def sent(self) -> list[Any]:
        return self.control.repos.sent_emails.list() if hasattr(self.control.repos.sent_emails, "list") else \
            self.control.db.fetchall("SELECT * FROM sent_emails")
