"""Trusted protected-execution core (runs only inside the executor service).

Static registry: only tools in both the security policy and ``mock_tools._TOOL_FUNCTIONS`` can run;
parameters are re-validated from the *stored* action, never from the network request. Effects of the
simulated tools and the ``SUCCEEDED`` transition commit in one database transaction, so for these
tools an operation is applied iff it is SUCCEEDED. A crash after ``EXECUTING`` is committed but before
completion leaves ``EXECUTING``; recovery marks it ``UNKNOWN`` and never re-executes (at-most-once).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from swarmguard.agents.workspace import SharedWorkspace, WorkspaceError
from swarmguard.clock import Clock
from swarmguard.execution.grants import GrantError, authorization_digest, verify_grant
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.models.events import EventType
from swarmguard.models.workflows import WorkflowStatus
from swarmguard.security.approval_manager import ApprovalError, ApprovalManager
from swarmguard.security.identity import IdentityRegistry
from swarmguard.security.policy_engine import DEFAULT_POLICY, PolicyEngine, SecurityPolicy
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import (FINAL_OPERATION_STATES, ControlStateRepository,
                                             ProtectedOperationRepository, Repositories)
from swarmguard.tools.mock_tools import REGISTERED_TOOL_NAMES, MockToolExecutor, PermitError, PreconditionFailed
from swarmguard.authentication.keys import random_secret

logger = logging.getLogger("swarmguard.executor")
EXECUTOR_ACTOR = "swarmguard.executor"
SECURITY_STREAM = "system_security"


@dataclass
class ExecutionReply:
    http_status: int
    state: str | None
    reason_code: str | None
    result: dict[str, Any] | None = None
    detail: str = ""

    def body(self, operation_id: str | None) -> dict[str, Any]:
        return {"operation_id": operation_id, "state": self.state, "reason_code": self.reason_code,
                "result": self.result, "detail": self.detail}


class _Superseded(RuntimeError):
    pass


class ProtectedExecutor:
    def __init__(self, *, db: Database, clock: Clock, grant_public_key: Ed25519PublicKey, approval_signing_key: bytes,
                 approver_ids: list[str], policy: SecurityPolicy = DEFAULT_POLICY) -> None:
        self.db, self.clock = db, clock
        self._grant_key = grant_public_key
        self.repos = Repositories(db)
        self.monitor = WorkflowMonitor(db, clock, self.repos)
        self.workspace = SharedWorkspace(db, self.monitor, clock, self.repos)
        self.approvals = ApprovalManager(db, self.monitor, clock, IdentityRegistry(), signing_key=approval_signing_key,
                                         repos=self.repos)
        from swarmguard.authentication.service_identity import assert_disjoint_approvers
        assert_disjoint_approvers(db, approver_ids)
        for approver in approver_ids:   # trusted approver IDs (configuration); credentials stay in the control plane
            self.approvals.register_approver(approver)
        self.approvals.seal()
        self.policy = PolicyEngine(policy)
        self.ops = ProtectedOperationRepository(db)
        self.control = ControlStateRepository(db)
        self._tools = MockToolExecutor(permit_key=random_secret(), workspace=self.workspace, approvals=self.approvals,
                                       monitor=self.monitor, repos=self.repos, db=db, clock=clock)

    def _now(self) -> str:
        return self.clock.now().isoformat()

    def _fail(self, op_id: str, reason: ReasonCode, detail: str, *, from_state: str = "AUTHORIZED") -> ExecutionReply:
        self.ops.transition(op_id, from_states=(from_state,), to_state="FAILED", actor=EXECUTOR_ACTOR, at=self._now(),
                            reason_code=reason.value, result={"error": detail})
        current = self.ops.get(op_id)
        return ExecutionReply(200, current["state"] if current else None,
                              current["reason_code"] if current else reason.value, None, detail)

    def _current(self, op: dict[str, Any]) -> ExecutionReply:
        status = 409 if op["state"] in ("EXECUTING", "PENDING") else 200
        return ExecutionReply(status, op["state"], op["reason_code"], op["result"] if op["state"] == "SUCCEEDED" else None,
                              "operation already processed; no new execution")

    def execute(self, grant_token: str | None) -> tuple[ExecutionReply, str | None]:
        """Verify, consume and run one granted operation. Returns (reply, operation_id)."""
        reply, op = self.begin(grant_token)
        if op is None:
            return reply, None
        return self.complete(op), op["operation_id"]

    def begin(self, grant_token: str | None) -> tuple[ExecutionReply, dict[str, Any] | None]:
        now = self.clock.now()
        try:
            claims = verify_grant(self._grant_key, grant_token, now=now)
        except GrantError as exc:
            self._security_alert(None, f"Executor rejected a request without a valid gateway grant: {exc}")
            return ExecutionReply(401, None, ReasonCode.EXECUTION_GRANT_INVALID.value, detail=str(exc)), None
        op = self.ops.get(claims.operation_id)
        if op is None or op["action_id"] != claims.action_id or op["authorization_digest"] != claims.digest:
            self._security_alert(claims.action_id, "Executor rejected a grant that does not match a durable operation.")
            return ExecutionReply(403, None, ReasonCode.EXECUTION_GRANT_INVALID.value,
                                  detail="grant does not match an authorized operation"), None
        if op["state"] != "AUTHORIZED":
            return self._current(op), None
        op_id = op["operation_id"]
        expires = op["grant_expires_at"]
        if expires is None or datetime.fromisoformat(expires) < now:
            return self._fail(op_id, ReasonCode.EXECUTION_GRANT_INVALID, "authorization expired before execution"), None
        stop = self.control.get("emergency_stop")
        if stop and stop[0].get("active"):
            return self._fail(op_id, ReasonCode.EMERGENCY_STOP_ACTIVE, "emergency stop active"), None
        disabled = self.control.get("disabled_capabilities")
        if disabled and op["tool"] in disabled[0].get("tools", []):
            return self._fail(op_id, ReasonCode.CAPABILITY_DISABLED, f"{op['tool']} disabled by operator"), None
        if op["tool"] not in REGISTERED_TOOL_NAMES or self.policy.tool_spec(op["tool"]) is None:
            return self._fail(op_id, ReasonCode.UNKNOWN_TOOL, "tool not in the static executor registry"), None
        request = self.monitor.get_action(op["action_id"])
        decision = self.monitor.get_decision_for_action(op["action_id"])
        column = self.db.fetchone("SELECT decision FROM decisions WHERE action_id = ?", (op["action_id"],))
        if column is None or column[0] != Decision.ALLOW.value:
            decision = None   # indexed column and stored record must agree on ALLOW
        if request is None or decision is None or decision.decision is not Decision.ALLOW or \
                decision.policy_decision is not Decision.ALLOW or request.tool != op["tool"]:
            return self._fail(op_id, ReasonCode.EXECUTION_GRANT_INVALID, "no persisted ALLOW decision for this action"), None
        if authorization_digest(request, op["expected"]) != op["authorization_digest"]:
            return self._fail(op_id, ReasonCode.EXECUTION_GRANT_INVALID, "stored action differs from the authorization"), None
        workflow = self.monitor.get_workflow(request.workflow_id)
        if workflow is None or workflow.status is not WorkflowStatus.ACTIVE:
            # Cancellation policy: an authorized but unstarted operation of a paused/halted workflow never starts.
            return self._fail(op_id, ReasonCode.WORKFLOW_NOT_ACTIVE, "workflow is no longer active"), None
        if not self.ops.transition(op_id, from_states=("AUTHORIZED",), to_state="EXECUTING", actor=EXECUTOR_ACTOR,
                                   at=self._now()):
            current = self.ops.get(op_id)
            return self._current(current), None   # lost the race: someone else consumed or cancelled it
        return ExecutionReply(202, "EXECUTING", None), {**op, "state": "EXECUTING", "_request": request}

    def complete(self, op: dict[str, Any]) -> ExecutionReply:
        op_id, request = op["operation_id"], op["_request"]
        spec = self.policy.tool_spec(request.tool)
        try:
            params = spec.params_model.model_validate(request.parameters)   # type: ignore[union-attr]
            permit = self._tools.issue_permit(request.action_id, request.tool, op["expected"])
            with self.db.transaction():
                result = self._tools.execute(request, params, permit)
                if not self.ops.transition(op_id, from_states=("EXECUTING",), to_state="SUCCEEDED", actor=EXECUTOR_ACTOR,
                                           at=self._now(), result=result):
                    raise _Superseded("operation was resolved elsewhere (e.g. marked UNKNOWN); effects rolled back")
        except _Superseded as exc:
            current = self.ops.get(op_id) or {}
            return ExecutionReply(200, current.get("state"), current.get("reason_code"), None, str(exc))
        except (PreconditionFailed, ApprovalError, WorkspaceError, PermitError, ValueError) as exc:
            return self._fail(op_id, ReasonCode.EXECUTION_PRECONDITION_FAILED, f"{type(exc).__name__}: {exc}",
                              from_state="EXECUTING")
        except Exception as exc:  # noqa: BLE001 - transaction rolled back; recorded as a failure
            logger.exception("executor error for %s", op_id)
            return self._fail(op_id, ReasonCode.EXECUTION_ERROR, type(exc).__name__, from_state="EXECUTING")
        return ExecutionReply(200, "SUCCEEDED", None, result)

    def _security_alert(self, action_id: str | None, summary: str) -> None:
        try:
            self.monitor.record_event(EventType.SECURITY_ALERT, SECURITY_STREAM, agent_id=EXECUTOR_ACTOR,
                                      parent_event_ids=[], summary=summary[:500],
                                      reason_code=ReasonCode.EXECUTION_GRANT_INVALID.value,
                                      details={"claimed_action_id": action_id})
        except Exception:  # noqa: BLE001 - auditing failure must not turn a rejection into an acceptance
            logger.exception("could not record executor security alert")

    def recover(self) -> dict[str, int]:
        """Startup recovery: EXECUTING without completion -> UNKNOWN. Never re-executes."""
        changed = 0
        for op in self.ops.list(state="EXECUTING"):
            if self.ops.transition(op["operation_id"], from_states=("EXECUTING",), to_state="UNKNOWN",
                                   actor="swarmguard.recovery", at=self._now(),
                                   reason_code=ReasonCode.EXECUTION_OUTCOME_UNKNOWN.value):
                changed += 1
        return {"executing_marked_unknown": changed}


__all__ = ["ProtectedExecutor", "ExecutionReply", "FINAL_OPERATION_STATES"]
