"""Gateway startup recovery. Never re-executes and never re-authorizes; only closes or reports.

* PENDING (request received, no authorization committed)      -> FAILED (not executed)
* AUTHORIZED (grant possibly issued, not consumed)             -> FAILED by CAS; the executor can no longer
                                                                  consume it, so no replay is possible
* EXECUTING                                                    -> left to the executor (it may still finish);
                                                                  the executor's own recovery marks UNKNOWN
* SUCCEEDED but the gateway decision not yet marked executed   -> decision reconciled to executed
"""
from __future__ import annotations
from typing import Any
from swarmguard.clock import Clock
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.repositories import ProtectedOperationRepository

RECOVERY_ACTOR = "swarmguard.recovery"


def interrupted_response(op: dict[str, Any], reason: ReasonCode) -> dict[str, Any]:
    return {"operation_id": op["operation_id"], "action_id": op.get("action_id"), "workflow_id": op["workflow_id"],
            "tool": op["tool"], "decision": Decision.BLOCK.value, "reason_code": reason.value,
            "explanation": "Interrupted by a service restart before execution; not executed. Submit a new request.",
            "details": {}, "executed": False, "execution_result": None, "execution_state": "FAILED"}


def recover_gateway(ops: ProtectedOperationRepository, monitor: WorkflowMonitor, clock: Clock) -> dict[str, int]:
    summary = {"pending_closed": 0, "authorized_cancelled": 0, "succeeded_reconciled": 0, "executing_left": 0}
    now = clock.now().isoformat()
    for state, key in (("PENDING", "pending_closed"), ("AUTHORIZED", "authorized_cancelled")):
        for op in ops.list(state=state, limit=10_000):
            if ops.transition(op["operation_id"], from_states=(state,), to_state="FAILED", actor=RECOVERY_ACTOR, at=now,
                              reason_code=ReasonCode.INTERNAL_ERROR.value,
                              response=interrupted_response(op, ReasonCode.INTERNAL_ERROR)):
                summary[key] += 1
                if op.get("action_id"):
                    decision = monitor.get_decision_for_action(op["action_id"])
                    if decision is not None and not decision.executed:
                        monitor.update_decision_execution(decision.decision_id, executed=False,
                                                          status="FAILED:INTERRUPTED_BY_RESTART")
    for op in ops.list(state="SUCCEEDED", limit=10_000):
        decision = monitor.get_decision_for_action(op["action_id"]) if op.get("action_id") else None
        if decision is not None and not decision.executed:
            monitor.update_decision_execution(decision.decision_id, executed=True, status="COMPLETED")
            summary["succeeded_reconciled"] += 1
    summary["executing_left"] = len(ops.list(state="EXECUTING", limit=10_000))
    return summary
