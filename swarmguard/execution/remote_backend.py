"""Gateway-side execution backend: durable authorization, signed grant, bounded safe delivery.

Retries only *re-deliver the same grant for the same operation*; the executor's compare-and-set
consumption makes redelivery idempotent, so a retry can never cause a second execution. When no
definitive answer arrives, the gateway arbitrates through the database:

* ``AUTHORIZED -> FAILED`` succeeds  => the executor never started; definitively not executed.
* the operation is ``EXECUTING``      => ``EXECUTING -> UNKNOWN``; reported as UNKNOWN, never retried.
* the operation is ``SUCCEEDED``      => late success is reported as executed.
"""
from __future__ import annotations
import asyncio
import contextvars
import logging
from datetime import timedelta
from typing import Any, Protocol
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel
from swarmguard.clock import Clock
from swarmguard.execution.errors import ExecutionOutcomeUnknown, ExecutionRefused, ExecutorUnavailable
from swarmguard.execution.grants import GRANT_TTL, authorization_digest, issue_grant
from swarmguard.models.actions import ActionRequest
from swarmguard.models.decisions import ReasonCode
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ProtectedOperationRepository

logger = logging.getLogger("swarmguard.gateway.execution")
GATEWAY_ACTOR = "swarmguard.gateway"
CURRENT_OPERATION: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("swarmguard_operation",
                                                                                         default=None)


class ChannelError(ConnectionError):
    """Transport-level failure (no response received)."""


class ExecutorChannel(Protocol):
    """Internal gateway->executor transport. Implemented outside ``swarmguard/`` (``swarmguard_services``)
    so the core package keeps no network imports; injected by the service entry point."""

    async def execute(self, grant: str, correlation_id: str) -> tuple[int, dict[str, Any]]: ...

    async def live(self) -> bool: ...


class RemoteExecutionBackend:
    def __init__(self, *, db: Database, clock: Clock, grant_key: Ed25519PrivateKey, channel: ExecutorChannel,
                 max_attempts: int = 3, backoff_seconds: float = 0.1, grant_ttl: timedelta = GRANT_TTL) -> None:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        self._ops = ProtectedOperationRepository(db)
        self._clock = clock
        self._key = grant_key
        self.channel = channel
        self._attempts = max_attempts
        self._backoff = backoff_seconds
        self._ttl = grant_ttl
        self.fault: str | None = None   # test-only fault injection: "drop_response" simulates a lost reply

    def _now(self) -> str:
        return self._clock.now().isoformat()

    async def execute(self, request: ActionRequest, params: BaseModel, expected: dict[str, Any]) -> dict[str, Any]:
        context = CURRENT_OPERATION.get()
        if context is None:
            raise ExecutionRefused(ReasonCode.INTERNAL_ERROR, "remote execution requires a durable operation context")
        op_id = context["operation_id"]
        digest = authorization_digest(request, expected)
        now = self._clock.now()
        if not self._ops.transition(op_id, from_states=("PENDING",), to_state="AUTHORIZED", actor=GATEWAY_ACTOR,
                                    at=now.isoformat(), action_id=request.action_id, authorization_digest=digest,
                                    expected=expected, grant_expires_at=(now + self._ttl).isoformat()):
            raise ExecutionRefused(ReasonCode.INTERNAL_ERROR, "operation is not pending; refusing to authorize twice")
        # From here on the operation may be consumed by the executor: every failure path must arbitrate
        # through the durable record and may only ever conclude FAILED (provably unconsumed) or UNKNOWN.
        try:
            grant = issue_grant(self._key, operation_id=op_id, action_id=request.action_id, digest=digest, now=now,
                                ttl=self._ttl)
            reply = await self._deliver(grant, context.get("correlation_id", op_id))
        except Exception:  # noqa: BLE001 - unexpected failure after authorization: arbitrate, never assume
            logger.exception("unexpected failure delivering %s", op_id)
            reply = None
        if reply is not None:
            state = reply.get("state")
            if state == "SUCCEEDED":
                return dict(reply.get("result") or {})
            if state == "FAILED":
                raise ExecutionRefused(ReasonCode(reply.get("reason_code") or "EXECUTION_ERROR"),
                                       str(reply.get("detail") or "executor refused the operation"))
            if state is None and reply.get("_status") in (401, 403):
                self._ops.transition(op_id, from_states=("AUTHORIZED",), to_state="FAILED", actor=GATEWAY_ACTOR,
                                     at=self._now(), reason_code=ReasonCode.EXECUTION_GRANT_INVALID.value)
                raise ExecutionRefused(ReasonCode.EXECUTION_GRANT_INVALID, "executor rejected the execution grant")
        try:
            return self._arbitrate(op_id)
        except (ExecutionRefused, ExecutionOutcomeUnknown):
            raise
        except Exception as exc:  # noqa: BLE001 - arbitration impossible (e.g. database down): UNKNOWN
            raise ExecutionOutcomeUnknown(f"operation {op_id} could not be arbitrated: {type(exc).__name__}") from None

    async def _deliver(self, grant: str, correlation_id: str) -> dict[str, Any] | None:
        for attempt in range(self._attempts):
            try:
                status, body = await self.channel.execute(grant, correlation_id)
            except (ChannelError, Exception) as exc:  # noqa: BLE001 - no usable reply; redelivery is idempotent
                logger.warning("executor delivery attempt %d failed: %s", attempt + 1, type(exc).__name__)
            else:
                if self.fault == "drop_response":
                    return None   # simulated: the executor processed the request but the reply was lost
                if status < 500 and status != 409:
                    return {**body, "_status": status}
            await asyncio.sleep(self._backoff * (2 ** attempt))
        return None

    def _arbitrate(self, op_id: str) -> dict[str, Any]:
        if self._ops.transition(op_id, from_states=("AUTHORIZED",), to_state="FAILED", actor=GATEWAY_ACTOR,
                                at=self._now(), reason_code=ReasonCode.EXECUTOR_UNAVAILABLE.value):
            raise ExecutorUnavailable("executor unavailable; authorization cancelled before consumption")
        op = self._ops.get(op_id) or {}
        if op.get("state") == "SUCCEEDED":
            return dict(op.get("result") or {})
        if op.get("state") == "FAILED":
            raise ExecutionRefused(ReasonCode(op.get("reason_code") or "EXECUTION_ERROR"), "executor reported failure")
        if op.get("state") == "EXECUTING":
            self._ops.transition(op_id, from_states=("EXECUTING",), to_state="UNKNOWN", actor=GATEWAY_ACTOR,
                                 at=self._now(), reason_code=ReasonCode.EXECUTION_OUTCOME_UNKNOWN.value)
            op = self._ops.get(op_id) or {}
            if op.get("state") == "SUCCEEDED":
                return dict(op.get("result") or {})
        raise ExecutionOutcomeUnknown(f"operation {op_id} outcome cannot be established (state {op.get('state')})")
