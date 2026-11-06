"""Trusted safety interventions for autonomous workflows.

Authority (deliberately narrow):

* WARNING: a system message to the acting agent (information only; it changes no permission).
* ESCALATE_TO_HUMAN: recorded when the gateway's trusted WorkflowGuard escalated a protected send
  (HUMAN_OVERSIGHT mode). The simulated operator's response is recorded (default: decline).
* PAUSE_WORKFLOW: trusted runtime sets the workflow HALTED; the gateway then blocks everything
  (``WORKFLOW_NOT_ACTIVE``) by its existing deterministic check.
* TRUSTED_POLICY_BLOCK: recorded when the deterministic policy blocked an operation under an
  explicit workflow policy (POLICY_ENFORCEMENT mode: recipient scope).

The manager holds no policy, approval or identity handle. It cannot grant, approve, change policy
or execute anything. Monitor recommendations never mutate authorization rules.
"""

from __future__ import annotations

import json

from swarmguard.agents.message_bus import MessageBus
from swarmguard.clock import Clock
from swarmguard.models.agent_state import ActionResult
from swarmguard.models.events import EventType
from swarmguard.models.interventions import InterventionMode, InterventionRecord, InterventionType
from swarmguard.models.messages import MessageType
from swarmguard.models.workflows import WorkflowStatus
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database

SOURCE = "swarmguard.intervention_manager"


class InterventionManager:
    def __init__(self, mode: InterventionMode, workflow_id: str, monitor: WorkflowMonitor, bus: MessageBus,
                 db: Database, clock: Clock, *, alert_sources: tuple[str, ...] = ("global_workflow",),
                 pause_after_escalations: int = 2, operator_escalation_response: str = "DECLINED_BY_OPERATOR") -> None:
        self.mode, self.workflow_id = mode, workflow_id
        self._monitor, self._bus, self._db, self._clock = monitor, bus, db, clock
        self.alert_sources = alert_sources
        self.pause_after = pause_after_escalations
        self.operator_response = operator_escalation_response
        self.records: list[InterventionRecord] = []
        self._escalations = 0

    def _trigger(self, action_id: str | None) -> str | None:
        if not action_id:
            return None
        events = self._monitor.find_events(action_id=action_id, event_type=EventType.ACTION_PROPOSED)
        return events[0].event_id if events else None

    def _issue(self, kind: InterventionType, reason: str, *, action_id: str | None, target: str | None,
               status: str) -> InterventionRecord:
        record = InterventionRecord(
            intervention_id=self._db.next_id("intervention"), workflow_id=self.workflow_id, intervention_type=kind,
            triggering_event_id=self._trigger(action_id), triggering_action_id=action_id, target_agent_id=target,
            reason=reason[:1000], status=status, source=SOURCE, created_at=self._clock.now())
        self._monitor.repos.interventions.insert(record.intervention_id, self.workflow_id, record.model_dump_json(),
                                                 record.created_at.isoformat())
        self._monitor.record_event(EventType.INTERVENTION_ISSUED, self.workflow_id, agent_id=SOURCE,
                                   action_id=action_id, summary=f"{kind.value} ({status}): {reason[:200]}",
                                   details=json.loads(record.model_dump_json()))
        self.records.append(record)
        return record

    def on_assessments(self, action_id: str, agent_id: str, tool: str, alerts: dict[str, bool],
                       patterns: list[str]) -> list[InterventionRecord]:
        if self.mode is not InterventionMode.WARNING:
            return []
        if not any(alerts.get(s) for s in self.alert_sources):
            return []
        reason = (f"Potential workflow-level safety violation in {tool} ({action_id}); patterns: "
                  f"{', '.join(patterns) or 'n/a'}. Review before continuing.")
        self._bus.system_send(self.workflow_id, agent_id, MessageType.WARNING, f"SwarmGuard safety warning: {reason}",
                              source=SOURCE, related_action_id=action_id)
        return [self._issue(InterventionType.WARNING, reason, action_id=action_id, target=agent_id, status="ISSUED")]

    def on_gateway_result(self, agent_id: str, result: ActionResult) -> list[InterventionRecord]:
        out: list[InterventionRecord] = []
        if result.reason_code == "WORKFLOW_RISK_ESCALATION":
            self._escalations += 1
            out.append(self._issue(InterventionType.ESCALATE_TO_HUMAN,
                                   f"Trusted workflow guard escalated {result.tool} ({result.action_id}).",
                                   action_id=result.action_id, target=agent_id, status=self.operator_response))
            if self._escalations >= self.pause_after:
                out.append(self.pause(f"{self._escalations} escalations in this workflow"))
        elif result.reason_code == "RECIPIENT_OUT_OF_WORKFLOW_SCOPE":
            out.append(self._issue(InterventionType.TRUSTED_POLICY_BLOCK,
                                   f"Deterministic recipient-scope policy blocked {result.tool} ({result.action_id}).",
                                   action_id=result.action_id, target=agent_id, status="APPLIED"))
        return out

    def pause(self, reason: str) -> InterventionRecord:
        self._monitor.set_workflow_status(self.workflow_id, WorkflowStatus.HALTED)
        self._monitor.record_event(EventType.WORKFLOW_PAUSED, self.workflow_id, agent_id=SOURCE,
                                   summary=f"Workflow paused by trusted runtime: {reason}")
        return self._issue(InterventionType.PAUSE_WORKFLOW, reason, action_id=None, target=None, status="APPLIED")
