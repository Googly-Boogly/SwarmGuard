"""Cross-agent workflow monitor (Boundary C: trusted audit state).

Records every proposal, decision, resource read/write, approval transition,
and mock execution result. Each event carries explicit ``parent_event_ids``
written at record time by trusted code. The monitor records only what it
observes; it never invents events or infers agent intentions.

The SQLite log is a trusted local simulation record, NOT a tamper-proof ledger.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from swarmguard.clock import Clock
from swarmguard.models.actions import ActionRequest
from swarmguard.models.approvals import ApprovalRecord
from swarmguard.models.assessments import CouncilEvaluationRecord, CouncilReport, WorkflowContext
from swarmguard.models.decisions import Decision, DecisionRecord
from swarmguard.models.events import ActorType, EventType, WorkflowEvent
from swarmguard.models.workflows import SharedResource, WorkflowRecord, WorkflowStatus, WorkflowSummary
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories

logger = logging.getLogger("swarmguard.monitor")

GATEWAY_ACTOR = "swarmguard.gateway"


def infer_actor_type(agent_id: str) -> ActorType:
    return ActorType.SYSTEM if agent_id.startswith("swarmguard.") else ActorType.AGENT


class WorkflowMonitor:
    def __init__(self, db: Database, clock: Clock, repos: Repositories | None = None) -> None:
        self._db = db
        self._clock = clock
        self._repos = repos or Repositories(db)

    @property
    def repos(self) -> Repositories:
        return self._repos

    # ------------------------------------------------------------------ workflows

    def create_workflow(
        self, workflow_id: str, title: str, participants: list[str], *, created_by: str,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowRecord:
        record = WorkflowRecord(
            workflow_id=workflow_id, title=title, status=WorkflowStatus.ACTIVE, participants=sorted(participants),
            created_at=self._clock.now(), metadata=metadata or {},
        )
        with self._db.transaction():
            self._repos.workflows.insert(record)
            self.record_event(
                EventType.WORKFLOW_STARTED, workflow_id, agent_id=created_by,
                summary=f"Workflow '{title}' started with participants {record.participants}.",
                details={"participants": record.participants},
            )
        return record

    def get_workflow(self, workflow_id: str) -> WorkflowRecord | None:
        return self._repos.workflows.get(workflow_id)

    def list_workflows(self) -> list[WorkflowRecord]:
        return self._repos.workflows.list()

    def set_workflow_status(self, workflow_id: str, status: WorkflowStatus) -> None:
        self._repos.workflows.set_status(workflow_id, status)

    # ------------------------------------------------------------------ recording

    def record_event(
        self,
        event_type: EventType,
        workflow_id: str,
        *,
        agent_id: str,
        summary: str,
        actor_type: ActorType | None = None,
        action_id: str | None = None,
        tool: str | None = None,
        resource_id: str | None = None,
        resource_version_before: int | None = None,
        resource_version_after: int | None = None,
        approval_id: str | None = None,
        decision: str | None = None,
        reason_code: str | None = None,
        parent_event_ids: list[str] | None = None,
        related_event_ids: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> WorkflowEvent:
        """Append an event. If ``parent_event_ids`` is None and the event belongs to an
        action, its parent is that action's previous event (explicit per-action chain)."""
        with self._db.transaction():
            if parent_event_ids is None:
                head = self.head_event(action_id) if action_id else None
                parent_event_ids = [head.event_id] if head else []
            event = WorkflowEvent(
                event_id=self._db.next_id("event"),
                seq=self._repos.events.next_seq(),
                workflow_id=workflow_id,
                event_type=event_type,
                agent_id=agent_id,
                actor_type=actor_type or infer_actor_type(agent_id),
                action_id=action_id,
                tool=tool,
                resource_id=resource_id,
                resource_version_before=resource_version_before,
                resource_version_after=resource_version_after,
                approval_id=approval_id,
                decision=decision,
                reason_code=reason_code,
                parent_event_ids=sorted(set(parent_event_ids)),
                related_event_ids=related_event_ids or [],
                summary=summary,
                details=details or {},
                created_at=self._clock.now(),
            )
            self._repos.events.insert(event)
        logger.debug("event %s %s %s", event.event_id, event.event_type.value, event.summary)
        return event

    def head_event(self, action_id: str | None) -> WorkflowEvent | None:
        if action_id is None:
            return None
        events = self._repos.events.query(action_id=action_id)
        return events[-1] if events else None

    def record_action(self, request: ActionRequest) -> WorkflowEvent:
        parents = [h.event_id for h in (self.head_event(p) for p in request.parent_action_ids) if h is not None]
        with self._db.transaction():
            self._repos.actions.insert(request)
            target = f" on {request.resource_id}" + (f" v{request.resource_version}" if request.resource_version else "")
            return self.record_event(
                EventType.ACTION_PROPOSED,
                request.workflow_id,
                agent_id=request.agent_id,
                action_id=request.action_id,
                tool=request.tool,
                resource_id=request.resource_id,
                resource_version_before=request.resource_version,
                approval_id=request.approval_id,
                parent_event_ids=parents,
                summary=f"{request.agent_id} proposed {request.tool}"
                + (target if request.resource_id else "")
                + (f" citing {request.approval_id}" if request.approval_id else "")
                + ".",
                details={"parameters": request.parameters, "parent_action_ids": request.parent_action_ids},
            )

    def record_decision(self, record: DecisionRecord) -> WorkflowEvent:
        with self._db.transaction():
            self._repos.decisions.upsert(record)
            return self.record_event(
                EventType.DECISION_RECORDED,
                record.workflow_id,
                agent_id=GATEWAY_ACTOR,
                action_id=record.action_id,
                tool=record.tool,
                decision=record.decision.value,
                reason_code=record.reason_code.value,
                summary=f"Gateway decision for {record.action_id} ({record.tool}): "
                f"{record.decision.value} [{record.reason_code.value}] {record.explanation}",
                details={
                    "decision_id": record.decision_id,
                    "policy_decision": record.policy_decision.value,
                    "policy_reason_code": record.policy_reason_code.value,
                    "advisory_recommendation": record.advisory_recommendation.value if record.advisory_recommendation else None,
                    **({"policy_details": record.details} if record.details else {}),
                },
            )

    def update_decision_execution(self, decision_id: str, *, executed: bool, status: str) -> None:
        record = self._repos.decisions.get(decision_id)
        if record is None:
            raise KeyError(decision_id)
        self._repos.decisions.upsert(record.model_copy(update={"executed": executed, "execution_status": status}))

    # ------------------------------------------------------------------ queries

    def record_council_evaluation(self, report: CouncilReport, *, snapshot_digest: str | None = None,
                                  experiment_id: str | None = None) -> CouncilEvaluationRecord:
        """Persist an advisory council result (Stage 3). Writes no workflow event, so recorded advice
        never becomes part of the history that later monitors observe."""
        record = CouncilEvaluationRecord(
            evaluation_id=self._db.next_id("ethics_eval"), workflow_id=report.workflow_id or "unknown",
            action_id=report.action_id, council_type=report.council_type, experiment_id=experiment_id,
            snapshot_digest=snapshot_digest, report=report, created_at=self._clock.now())
        self._repos.ethics.insert(record)
        return record

    def get_council_evaluations(self, workflow_id: str) -> list[CouncilEvaluationRecord]:
        return self._repos.ethics.for_workflow(workflow_id)

    def get_workflow_history(self, workflow_id: str) -> list[WorkflowEvent]:
        return self._repos.events.query(workflow_id=workflow_id)

    def get_agent_history(self, agent_id: str) -> list[WorkflowEvent]:
        return self._repos.events.query(agent_id=agent_id)

    def get_resource_history(self, resource_id: str) -> list[WorkflowEvent]:
        return self._repos.events.query(resource_id=resource_id)

    def get_event(self, event_id: str) -> WorkflowEvent | None:
        return self._repos.events.get(event_id)

    def find_events(self, **filters: Any) -> list[WorkflowEvent]:
        return self._repos.events.query(**filters)

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._repos.actions.get(action_id)

    def get_workflow_actions(self, workflow_id: str) -> list[ActionRequest]:
        return self._repos.actions.for_workflow(workflow_id)

    def get_decisions(self, workflow_id: str) -> list[DecisionRecord]:
        return self._repos.decisions.for_workflow(workflow_id)

    def get_decision_for_action(self, action_id: str) -> DecisionRecord | None:
        return self._repos.decisions.for_action(action_id)

    def get_related_actions(self, action_id: str) -> list[ActionRequest]:
        """Explicitly referenced relatives within the same workflow: ancestors and
        descendants via ``parent_action_ids``, actions citing the same approval, and
        actions that created or touched the same resource. Excludes the action itself."""
        target = self.get_action(action_id)
        if target is None:
            return []
        actions = {a.action_id: a for a in self.get_workflow_actions(target.workflow_id)}
        related: set[str] = set()

        stack = list(target.parent_action_ids)
        while stack:
            parent = stack.pop()
            if parent in actions and parent not in related:
                related.add(parent)
                stack.extend(actions[parent].parent_action_ids)

        stack = [action_id]
        while stack:
            current = stack.pop()
            for a in actions.values():
                if current in a.parent_action_ids and a.action_id not in related:
                    related.add(a.action_id)
                    stack.append(a.action_id)

        for a in actions.values():
            if target.approval_id and a.approval_id == target.approval_id:
                related.add(a.action_id)
            if target.resource_id and a.resource_id == target.resource_id:
                related.add(a.action_id)
        if target.resource_id:
            for e in self._repos.events.query(resource_id=target.resource_id, event_type=EventType.RESOURCE_CREATED):
                if e.action_id in actions:
                    related.add(e.action_id)

        related.discard(action_id)
        return sorted((actions[i] for i in related), key=lambda a: (a.created_at, a.action_id))

    def get_workflow_summary(self, workflow_id: str) -> WorkflowSummary | None:
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return None
        events = self.get_workflow_history(workflow_id)
        decisions = self.get_decisions(workflow_id)
        approvals = self._repos.approvals.list(workflow_id=workflow_id)
        resources = self._repos.resources.list(workflow_id=workflow_id)
        return WorkflowSummary(
            workflow=workflow,
            event_count=len(events),
            events_by_type=dict(sorted(Counter(e.event_type.value for e in events).items())),
            agents_involved=sorted({e.agent_id for e in events if e.actor_type is ActorType.AGENT}),
            action_count=len(self.get_workflow_actions(workflow_id)),
            decisions_by_outcome=dict(sorted(Counter(d.decision.value for d in decisions).items())),
            blocked_actions=[
                {"action_id": d.action_id, "agent_id": d.agent_id, "tool": d.tool, "reason_code": d.reason_code.value}
                for d in decisions if d.decision is not Decision.ALLOW
            ],
            approvals=[
                {"approval_id": a.approval_id, "status": a.status.value, "resource_id": a.resource_id,
                 "resource_version": a.resource_version, "approver_id": a.approver_id}
                for a in approvals
            ],
            resources=[
                {"resource_id": r.resource_id, "resource_type": r.resource_type.value, "version": r.version,
                 "updated_by": r.updated_by}
                for r in resources
            ],
            simulated_emails_sent=len(self._repos.sent_emails.list(workflow_id)),
        )

    def build_workflow_context(
        self, request: ActionRequest, *, resource: SharedResource | None, approvals: list[ApprovalRecord]
    ) -> WorkflowContext:
        return WorkflowContext(
            workflow_id=request.workflow_id,
            action=request,
            history=self.get_workflow_history(request.workflow_id),
            related_actions=self.get_related_actions(request.action_id),
            resource=resource,
            resource_history=self.get_resource_history(request.resource_id) if request.resource_id else [],
            resource_versions=self._repos.resources.versions(request.resource_id) if request.resource_id else [],
            approvals=approvals,
        )

    def render_history(self, workflow_id: str, *, include_types: set[EventType] | None = None) -> str:
        lines = [f"Workflow: {workflow_id}", ""]
        n = 0
        for event in self.get_workflow_history(workflow_id):
            if include_types and event.event_type not in include_types:
                continue
            n += 1
            lines.append(f"Event {n} [{event.event_id}] {event.event_type.value} ({event.agent_id}):")
            lines.append(f"  {event.summary}")
        return "\n".join(lines)
