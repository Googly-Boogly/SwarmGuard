"""Trusted autonomous-agent runtime: builds permitted contexts and routes decisions through the gateway.

Routing (every side effect is an ``ActionProposal`` submitted with the agent's own credential):

    TOOL_REQUEST        -> <tool>
    SEND_MESSAGE        -> send_message
    REQUEST_INFORMATION -> send_message (message_type REQUEST)
    DELEGATE_TASK       -> assign_task
    REQUEST_APPROVAL    -> request_approval(operation=send_email)
    COMPLETE_TASK       -> complete_task (or, coordinator with task_id null: objective declared finished,
                           which has no side effect and is never used as proof of success)
    WAIT                -> nothing

A decision that is invalid, or cannot be mapped, is recorded and NOT submitted. The decision's
``claimed_agent_id`` is passed to the gateway only as a claim; a mismatch is blocked there.
"""

from __future__ import annotations

import json
from typing import Any

from swarmguard.agents.autonomous_agent import AgentContext, DecisionOutcome
from swarmguard.agents.message_bus import MessageBus
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.agents.task_manager import TaskManager
from swarmguard.models.actions import ActionProposal
from swarmguard.models.agent_state import ActionResult, AgentDecision, DecisionType
from swarmguard.models.events import EventType
from swarmguard.models.messages import MessageType
from swarmguard.models.tasks import TaskStatus
from swarmguard.models.workflows import ResourceType

RUNTIME_ACTOR = "swarmguard.agent_runtime"


class DecisionMappingError(ValueError):
    pass


def to_proposal(workflow_id: str, d: AgentDecision) -> ActionProposal | None:
    """Map a validated decision to a strict ``ActionProposal`` (None for WAIT / objective completion)."""
    claim = d.claimed_agent_id
    common: dict[str, Any] = {"workflow_id": workflow_id, "parent_action_ids": list(dict.fromkeys(d.parent_action_ids)),
                              **({"agent_id": claim} if claim else {})}
    t = d.decision_type
    if t is DecisionType.WAIT:
        return None
    if t is DecisionType.TOOL_REQUEST:
        if not d.tool:
            raise DecisionMappingError("TOOL_REQUEST without a tool")
        return ActionProposal(tool=d.tool, resource_id=d.resource_id, resource_version=d.resource_version,
                              parameters=d.parameters(), approval_id=d.approval_id, **common)
    if t in (DecisionType.SEND_MESSAGE, DecisionType.REQUEST_INFORMATION):
        if not d.recipient_id or not d.message_text:
            raise DecisionMappingError("message without recipient or text")
        mtype = MessageType.REQUEST if t is DecisionType.REQUEST_INFORMATION else (d.message_type or MessageType.INFORMATION)
        params = {"recipient": d.recipient_id, "message_type": mtype.value, "text": d.message_text}
        parent = d.parameters().get("parent_message_id")
        if parent:
            params["parent_message_id"] = parent
        return ActionProposal(tool="send_message", parameters=params, **common)
    if t is DecisionType.DELEGATE_TASK:
        if not d.recipient_id or not d.task_description:
            raise DecisionMappingError("delegation without assignee or description")
        return ActionProposal(tool="assign_task", parameters={
            "assignee": d.recipient_id, "description": d.task_description,
            "depends_on_task_ids": list(d.depends_on_task_ids), "required_tools": list(d.required_tools)}, **common)
    if t is DecisionType.REQUEST_APPROVAL:
        return ActionProposal(tool="request_approval", resource_id=d.resource_id, resource_version=d.resource_version,
                              parameters={"operation": "send_email"}, **common)
    if t is DecisionType.COMPLETE_TASK:
        if d.task_id is None:
            return None
        return ActionProposal(tool="complete_task", resource_id=d.task_id, resource_version=d.resource_version,
                              parameters={"summary": (d.summary or "")[:2000]}, **common)
    raise DecisionMappingError(f"unsupported decision type {t}")


class AgentRuntime:
    def __init__(self, sim: SwarmSimulator, workflow_id: str, tasks: TaskManager, bus: MessageBus) -> None:
        self.sim, self.workflow_id, self.tasks, self.bus = sim, workflow_id, tasks, bus
        self.objective_declared_complete = False

    # ------------------------------------------------------------------ permitted context

    def build_context(self, agent_id: str, *, step: int, steps_remaining: int, objective: str | None,
                      objective_fields: dict[str, Any], private_objective: str | None = None,
                      private_objective_fields: dict[str, Any] | None = None) -> AgentContext:
        wf = self.sim.monitor.get_workflow(self.workflow_id)
        assert wf is not None
        identity = self.sim.identities.get(agent_id)
        assert identity is not None
        all_tasks = self.tasks.tasks(self.workflow_id)
        visible = all_tasks if identity.role.value == "coordinator" else [
            t for t in all_tasks if t.assigned_to == agent_id]
        readable = []
        for r in sorted(self.sim.workspace.list(), key=lambda r: r.resource_id):
            if (r.workflow_id in (None, self.workflow_id)) and r.acl.can_read(agent_id) and \
                    r.resource_type is not ResourceType.WORKFLOW_METADATA:
                if r.workflow_id is None and r.resource_type is ResourceType.DOCUMENT:
                    continue  # global reference documents are not part of the task environment
                readable.append({"resource_id": r.resource_id, "resource_type": r.resource_type.value,
                                 "version": r.version})
        approvals = [{"approval_id": a.approval_id, "resource_id": a.resource_id,
                      "resource_version": a.resource_version, "status": a.status.value}
                     for a in self.sim.approvals.list_for_workflow(self.workflow_id) if a.requested_by == agent_id]
        inbox = [{"message_id": m.message_id, "from": m.sender_id, "message_type": m.message_type.value,
                  "text": m.text, "parent_message_id": m.parent_message_id, "is_system": m.is_system}
                 for m in self.bus.inbox(self.workflow_id, agent_id)]
        return AgentContext(
            workflow_id=self.workflow_id, step=step, steps_remaining=steps_remaining, objective=objective,
            objective_fields=objective_fields, private_objective=private_objective,
            private_objective_fields=private_objective_fields or {},
            allowed_tools=sorted(self.sim.policy_engine.permissions_for(identity.role)),
            workflow_participants=list(wf.participants),
            tasks=[t.model_dump(mode="json") for t in visible], inbox=inbox, readable_resources=readable,
            my_approval_requests=approvals)

    # ------------------------------------------------------------------ routing

    async def process(self, agent_id: str, outcome: DecisionOutcome, step: int) -> ActionResult:
        d = outcome.decision
        if d is None:
            return self._record(agent_id, step, outcome, ActionResult(
                step=step, decision_type=DecisionType.WAIT, accepted=False,
                explanation=f"invalid or failed decision ({outcome.error_kind}): {outcome.error}"), rejected=True)
        try:
            proposal = to_proposal(self.workflow_id, d)
        except (DecisionMappingError, ValueError) as exc:
            return self._record(agent_id, step, outcome, ActionResult(
                step=step, decision_type=d.decision_type, accepted=False,
                explanation=f"decision could not be mapped to a proposal: {str(exc)[:300]}"), rejected=True)
        if proposal is None:
            if d.decision_type is DecisionType.COMPLETE_TASK:
                identity = self.sim.identities.get(agent_id)
                if identity is None or identity.role.value != "coordinator":
                    return self._record(agent_id, step, outcome, ActionResult(
                        step=step, decision_type=d.decision_type, accepted=False,
                        explanation="only the coordinator can declare the objective finished"), rejected=True)
                self.objective_declared_complete = True
            elif d.decision_type is DecisionType.WAIT:
                for t in self.tasks.for_agent(self.workflow_id, agent_id):
                    if t.status in (TaskStatus.READY, TaskStatus.RUNNING):
                        self.tasks.mark(self.workflow_id, t.task_id, TaskStatus.WAITING, f"{agent_id} waiting")
            return self._record(agent_id, step, outcome, ActionResult(step=step, decision_type=d.decision_type,
                                                                      accepted=True, explanation="no side effect"))
        for t in self.tasks.for_agent(self.workflow_id, agent_id):
            if t.status in (TaskStatus.READY, TaskStatus.WAITING):
                self.tasks.mark(self.workflow_id, t.task_id, TaskStatus.RUNNING, f"{agent_id} acting")
        response = await self.sim.gateway_client(agent_id).submit(proposal)
        return self._record(agent_id, step, outcome, ActionResult(
            step=step, decision_type=d.decision_type, accepted=True, action_id=response.action_id, tool=response.tool,
            decision=response.decision.value, reason_code=response.reason_code.value, executed=response.executed,
            explanation=response.explanation, result=response.execution_result,
            proposal=proposal.model_dump(mode="json", exclude={"agent_id"})))

    def _record(self, agent_id: str, step: int, outcome: DecisionOutcome, result: ActionResult, *,
                rejected: bool = False) -> ActionResult:
        d = outcome.decision
        record = {
            "workflow_id": self.workflow_id, "agent_id": agent_id, "step": step, "valid": not rejected,
            "decision_type": d.decision_type.value if d else None, "summary": d.summary if d else None,
            "claimed_agent_id": d.claimed_agent_id if d else None, "raw_decision": outcome.raw,
            "error_kind": outcome.error_kind, "error": outcome.error, "provider": outcome.provider,
            "result": result.model_dump(mode="json"),
        }
        record_id = self.sim.db.next_id("agent_decision")
        record["decision_record_id"] = record_id
        self.sim.repos.agent_decisions.insert(record_id, self.workflow_id, agent_id, step,
                                              json.dumps(record, sort_keys=True, default=str),
                                              self.sim.clock.now().isoformat())
        if rejected:
            self.sim.monitor.record_event(
                EventType.AGENT_DECISION_REJECTED, self.workflow_id, agent_id=RUNTIME_ACTOR,
                summary=f"Decision of {agent_id} at step {step} rejected before submission: {result.explanation[:200]}",
                details={"agent_id": agent_id, "step": step, "decision_record_id": record_id,
                         "error_kind": outcome.error_kind})
        return result
