"""Remote capability gateway (Stage 7): authenticated, idempotent access to the existing gateway.

Order of checks for ``POST /api/v1/capabilities/execute``:

1. authenticate the bearer credential (signature, audience, expiry, revocation, binding)
2. per-agent rate limit and concurrency limit (trusted identity keys only)
3. strict request schema (``agent_id``, roles or authorization flags are rejected, not ignored)
4. binding checks: workflow in the agent's authorized workflows; tool in its granted capabilities
5. operator controls: emergency stop and disabled capabilities
6. idempotency: ``(agent_id, request_id)`` is unique; same digest => stored result, different => BLOCK
7. the unchanged ``CapabilityGateway`` (membership, permissions, resources, versions, approvals, policy)
8. remote execution through the gateway-signed, durably recorded operation

Every refusal before step 7 is persisted through ``CapabilityGateway.record_rejection``. If the trusted
database is unavailable nothing can be authorized or recorded: the request fails closed with
``AUTHORIZATION_SERVICE_UNAVAILABLE`` and nothing executes.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from swarmguard.authentication.service_identity import AuthenticatedAgent, AuthenticationError, ServiceIdentityVerifier
from swarmguard.clock import Clock
from swarmguard.execution.remote_backend import CURRENT_OPERATION
from swarmguard.infrastructure.metrics import ServiceMetrics
from swarmguard.infrastructure.rate_limits import RateLimiter
from swarmguard.models.actions import ID_PATTERN, TOOL_NAME_PATTERN
from swarmguard.models.decisions import Decision, GatewayResponse, ReasonCode
from swarmguard.models.events import EventType
from swarmguard.security.capability_gateway import CapabilityGateway
from swarmguard.security.identity import AgentCredential, AgentIdentity, IdentityRegistry
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import DATABASE_ERRORS, Database
from swarmguard.storage.repositories import FINAL_OPERATION_STATES, ControlStateRepository, ProtectedOperationRepository

logger = logging.getLogger("swarmguard.remote_gateway")
SECURITY_STREAM = "system_security"
GATEWAY_ACTOR = "swarmguard.gateway"


class RemoteCapabilityRequest(BaseModel):
    """Agent-supplied, untrusted. Identity is never part of the body."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    request_id: str = Field(pattern=ID_PATTERN)              # idempotency key, unique per agent
    workflow_id: str = Field(pattern=ID_PATTERN)
    tool: str = Field(pattern=TOOL_NAME_PATTERN)
    resource_id: str | None = Field(default=None, pattern=ID_PATTERN)
    resource_version: int | None = Field(default=None, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = Field(default=None, pattern=ID_PATTERN)
    parent_action_ids: list[str] = Field(default_factory=list, max_length=32)
    action_id: str | None = Field(default=None, pattern=ID_PATTERN)   # client correlation reference only

    def digest(self) -> str:
        body = self.model_dump(exclude={"action_id"})
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class RemoteResult:
    http_status: int
    body: dict[str, Any]


@dataclass
class _Limits:
    agent: RateLimiter = field(default_factory=lambda: RateLimiter(capacity=60, refill_per_second=2.0))
    auth_failures: RateLimiter = field(default_factory=lambda: RateLimiter(capacity=20, refill_per_second=0.5))
    max_concurrent: int = 8


class RemoteCapabilityGateway:
    def __init__(self, *, db: Database, clock: Clock, gateway: CapabilityGateway, identities: IdentityRegistry,
                 monitor: WorkflowMonitor, verifier: ServiceIdentityVerifier, metrics: ServiceMetrics,
                 limits: _Limits | None = None) -> None:
        self.db, self.clock, self.gateway, self.monitor = db, clock, gateway, monitor
        self._identities = identities
        self._verifier = verifier
        self._credentials: dict[str, AgentCredential] = {}
        self.ops = ProtectedOperationRepository(db)
        self.control = ControlStateRepository(db)
        self.metrics = metrics
        self.limits = limits or _Limits()
        self._semaphore = asyncio.Semaphore(self.limits.max_concurrent)

    # ------------------------------------------------------------------ helpers

    def _credential(self, agent: AuthenticatedAgent) -> AgentCredential:
        """Lazily register a DB-bound agent in this process's in-memory registry (trusted code only)."""
        existing = self._identities.get(agent.agent_id)
        if existing is not None:
            if existing.role is not agent.role:
                raise AuthenticationError(ReasonCode.AGENT_NOT_BOUND, "binding role changed; restart required")
            return self._credentials[agent.agent_id]
        credential = self._identities.register(AgentIdentity(agent.agent_id, agent.role, agent.agent_id))
        self._credentials[agent.agent_id] = credential
        return credential

    def _block(self, http_status: int, *, workflow_id: Any, agent_id: str, tool: Any, reason: ReasonCode,
               explanation: str, request_id: Any = None) -> RemoteResult:
        response = self.gateway.record_rejection(workflow_id=workflow_id, agent_id=agent_id, tool=tool, reason=reason,
                                                 explanation=explanation)
        self.metrics.inc("blocked")
        self.metrics.inc(f"reason.{reason.value}")
        return RemoteResult(http_status, self._body(response, request_id=request_id, operation_id=None))

    @staticmethod
    def _body(response: GatewayResponse, *, request_id: Any, operation_id: str | None,
              replay: bool = False) -> dict[str, Any]:
        return {"request_id": request_id if isinstance(request_id, str) else None, "operation_id": operation_id,
                **response.model_dump(mode="json"), "idempotent_replay": replay}

    def _security_event(self, event_type: EventType, summary: str, **details: Any) -> None:
        self.monitor.record_event(event_type, SECURITY_STREAM, agent_id=GATEWAY_ACTOR, parent_event_ids=[],
                                  summary=summary[:500], reason_code=details.get("reason_code"), details=details)

    def _control_flags(self) -> tuple[bool, set[str]]:
        stop = self.control.get("emergency_stop")
        disabled = self.control.get("disabled_capabilities")
        return bool(stop and stop[0].get("active")), set(disabled[0].get("tools", [])) if disabled else set()

    # ------------------------------------------------------------------ entry point

    async def handle(self, authorization: str | None, raw_body: Any, *, peer: str) -> RemoteResult:
        started = time.perf_counter()
        self.metrics.inc("gateway_requests")
        try:
            return await self._handle(authorization, raw_body, peer=peer)
        except DATABASE_ERRORS:
            logger.exception("trusted state store unavailable; failing closed")
            self.metrics.inc("database_errors")
            self.metrics.inc("blocked")
            return RemoteResult(503, {"request_id": None, "operation_id": None, "decision": Decision.BLOCK.value,
                                      "reason_code": ReasonCode.AUTHORIZATION_SERVICE_UNAVAILABLE.value,
                                      "explanation": "Mandatory authorization state is unavailable; nothing executed.",
                                      "executed": False, "execution_state": None, "details": {}})
        finally:
            self.metrics.observe_latency((time.perf_counter() - started) * 1000)

    async def _handle(self, authorization: str | None, raw_body: Any, *, peer: str) -> RemoteResult:
        body = raw_body if isinstance(raw_body, dict) else {}
        try:
            agent = self._verifier.verify(authorization)
        except AuthenticationError as exc:
            self.metrics.inc("authentication_failures")
            if not self.limits.auth_failures.allow(f"peer:{peer}"):
                return RemoteResult(429, {"decision": "BLOCK", "reason_code": ReasonCode.RATE_LIMITED.value,
                                          "executed": False, "explanation": "too many failed authentications"})
            self._security_event(EventType.AUTHENTICATION_FAILED,
                                 f"Rejected capability request: {exc.detail}", reason_code=exc.reason.value,
                                 claimed_agent=exc.agent_hint, peer=peer)
            return self._block(401, workflow_id=body.get("workflow_id"), agent_id="unauthenticated",
                               tool=body.get("tool"), reason=exc.reason, explanation=f"Authentication failed: {exc.detail}",
                               request_id=body.get("request_id"))
        if not self.limits.agent.allow(f"agent:{agent.agent_id}"):
            return self._block(429, workflow_id=body.get("workflow_id"), agent_id=agent.agent_id, tool=body.get("tool"),
                               reason=ReasonCode.RATE_LIMITED, explanation="Per-agent request rate exceeded.",
                               request_id=body.get("request_id"))
        try:
            request = RemoteCapabilityRequest.model_validate(raw_body)
        except ValidationError as exc:
            return self._block(422, workflow_id=body.get("workflow_id"), agent_id=agent.agent_id, tool=body.get("tool"),
                               reason=ReasonCode.INVALID_ACTION,
                               explanation=f"Malformed capability request ({exc.error_count()} error(s)); identity, "
                                           "roles and authorization fields are not accepted.",
                               request_id=body.get("request_id"))
        common = {"workflow_id": request.workflow_id, "agent_id": agent.agent_id, "tool": request.tool,
                  "request_id": request.request_id}
        if request.workflow_id not in agent.workflows:
            return self._block(200, reason=ReasonCode.NOT_WORKFLOW_PARTICIPANT,
                               explanation="Workflow is not in this agent's registered authorization scope.", **common)
        if request.tool not in agent.capabilities:
            return self._block(200, reason=ReasonCode.CAPABILITY_NOT_GRANTED,
                               explanation=f"Capability '{request.tool}' was not granted to this agent binding.", **common)
        stopped, disabled = self._control_flags()
        if stopped:
            return self._block(200, reason=ReasonCode.EMERGENCY_STOP_ACTIVE,
                               explanation="Emergency stop is active; no new protected operations.", **common)
        if request.tool in disabled:
            return self._block(200, reason=ReasonCode.CAPABILITY_DISABLED,
                               explanation=f"Capability '{request.tool}' is disabled by an administrator.", **common)
        if self._semaphore.locked():
            return self._block(429, reason=ReasonCode.RATE_LIMITED,
                               explanation="Maximum concurrent protected operations reached.", **common)
        digest = request.digest()
        operation_id = self.db.next_id("operation")
        correlation = request.action_id or operation_id
        if not self.ops.create(operation_id=operation_id, agent_id=agent.agent_id, runtime_id=agent.runtime_id,
                               request_id=request.request_id, request_digest=digest, workflow_id=request.workflow_id,
                               tool=request.tool, correlation_id=correlation, at=self.clock.now().isoformat()):
            return self._replay(agent, request, digest)
        credential = self._credential(agent)
        async with self._semaphore:
            self.metrics.in_flight += 1
            token = CURRENT_OPERATION.set({"operation_id": operation_id, "correlation_id": correlation})
            try:
                proposal = {"workflow_id": request.workflow_id, "tool": request.tool, "resource_id": request.resource_id,
                            "resource_version": request.resource_version, "parameters": request.parameters,
                            "approval_id": request.approval_id, "parent_action_ids": request.parent_action_ids,
                            "request_id": request.request_id}
                response = await self.gateway.submit(proposal, credential)
            finally:
                CURRENT_OPERATION.reset(token)
                self.metrics.in_flight -= 1
        return self._finalize(operation_id, request, response)

    def _finalize(self, operation_id: str, request: RemoteCapabilityRequest, response: GatewayResponse) -> RemoteResult:
        now = self.clock.now().isoformat()
        op = self.ops.get(operation_id) or {}
        state = op.get("state", "PENDING")
        if state == "PENDING":
            final_state = "BLOCKED" if response.decision is not Decision.ALLOW or not response.executed else "SUCCEEDED"
            response = response.model_copy(update={"execution_state": final_state})
            self.ops.transition(operation_id, from_states=("PENDING",), to_state=final_state, actor=GATEWAY_ACTOR, at=now,
                                reason_code=response.reason_code.value, action_id=response.action_id,
                                response=self._body(response, request_id=request.request_id, operation_id=operation_id))
        else:
            response = response.model_copy(update={"execution_state": state})
            self.ops.transition(operation_id, from_states=(state,), to_state=state, actor=GATEWAY_ACTOR, at=now,
                                response=self._body(response, request_id=request.request_id, operation_id=operation_id))
        self.metrics.inc({Decision.ALLOW: "allowed", Decision.BLOCK: "blocked", Decision.ESCALATE: "escalated"}
                         [response.decision])
        self.metrics.inc(f"reason.{response.reason_code.value}")
        if response.execution_state in ("FAILED", "UNKNOWN") and response.decision is Decision.ALLOW or \
                response.reason_code in (ReasonCode.EXECUTION_ERROR, ReasonCode.EXECUTOR_UNAVAILABLE):
            self.metrics.inc("execution_failures")
        if response.tool == "request_approval" and response.executed:
            self.metrics.inc("approval_requests")
        return RemoteResult(200, self._body(response, request_id=request.request_id, operation_id=operation_id))

    def _replay(self, agent: AuthenticatedAgent, request: RemoteCapabilityRequest, digest: str) -> RemoteResult:
        existing = self.ops.by_request(agent.agent_id, request.request_id)
        if existing is None:   # lost race on operation_id allocation (should not happen); fail closed
            return self._block(409, workflow_id=request.workflow_id, agent_id=agent.agent_id, tool=request.tool,
                               reason=ReasonCode.INTERNAL_ERROR, explanation="Operation state conflict.",
                               request_id=request.request_id)
        if existing["request_digest"] != digest:
            return self._block(200, workflow_id=request.workflow_id, agent_id=agent.agent_id, tool=request.tool,
                               reason=ReasonCode.IDEMPOTENCY_KEY_REUSED,
                               explanation="request_id was already used for a different operation.",
                               request_id=request.request_id)
        self.metrics.inc("idempotent_replays")
        if existing["state"] in FINAL_OPERATION_STATES and existing.get("response"):
            return RemoteResult(200, {**existing["response"], "idempotent_replay": True})
        return RemoteResult(409, {"request_id": request.request_id, "operation_id": existing["operation_id"],
                                  "decision": None, "reason_code": None, "executed": False,
                                  "execution_state": existing["state"], "idempotent_replay": True,
                                  "explanation": "Operation already in progress; poll later. It will not be re-executed."})
