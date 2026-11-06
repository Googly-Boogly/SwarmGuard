"""Controlled observation builder: the only way monitor inputs are constructed.

1. ``snapshot(workflow_id, cutoff_seq)`` reconstructs the workflow *as of* a checkpoint
   from the persisted audit log. It includes only events with ``seq <= cutoff_seq``, the
   immutable resource versions those events reference (created at or before the cutoff),
   and approval statuses replayed from approval events up to the cutoff. Mutable rows
   (current approval status, current resource content) are never read directly, so
   later events cannot leak into an earlier checkpoint.
2. ``local(snapshot, action_id)`` and ``global_(snapshot, action_id)`` derive the two
   observation windows from the same snapshot. Paired comparisons therefore evaluate
   identical workflow states, and the snapshot digest is recorded with each assessment.

Monitors never receive a database, repository or monitor handle: they get only the
frozen ``MonitorContext``. The builder performs reads only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import WorkflowContext
from swarmguard.models.approvals import ApprovalStatus
from swarmguard.models.events import EventType, WorkflowEvent
from swarmguard.models.risk import (
    ApprovalView,
    MonitorContext,
    ObservationWindow,
    ResourceSnapshot,
    WorkflowInvariant,
    WorkflowSnapshot,
)
from swarmguard.security.invariants import DEFAULT_INVARIANTS
from swarmguard.security.workflow_monitor import WorkflowMonitor

LOCAL_EXCLUDED = ["workflow_history", "prior_actions", "resource_provenance", "other_resources",
                  "other_approvals", "workflow_purpose"]
GLOBAL_EXCLUDED = ["events_after_checkpoint", "events_of_other_workflows", "resource_versions_not_referenced"]

_APPROVAL_TRANSITIONS = {
    EventType.APPROVAL_REQUESTED: ApprovalStatus.PENDING,
    EventType.APPROVAL_GRANTED: ApprovalStatus.APPROVED,
    EventType.APPROVAL_REJECTED: ApprovalStatus.REJECTED,
    EventType.APPROVAL_INVALIDATED: ApprovalStatus.INVALIDATED,
    EventType.APPROVAL_EXPIRED: ApprovalStatus.EXPIRED,
    EventType.APPROVAL_CONSUMED: ApprovalStatus.CONSUMED,
}
NEUTRAL_START_SUMMARY = "Workflow started."


class ObservationError(LookupError):
    pass


class ObservationBuilder:
    def __init__(self, monitor: WorkflowMonitor, *,
                 invariants: Iterable[WorkflowInvariant] = DEFAULT_INVARIANTS,
                 agent_roles: Mapping[str, str] | None = None) -> None:
        self._monitor = monitor
        self._repos = monitor.repos
        self._invariants = list(invariants)
        self._roles = dict(agent_roles or {})
        self._seq_cache: dict[str, int | None] = {}

    @property
    def invariants(self) -> list[WorkflowInvariant]:
        return list(self._invariants)

    # ------------------------------------------------------------------ checkpoints

    def checkpoints(self, workflow_id: str) -> list[tuple[str, int]]:
        """(action_id, seq of its ACTION_PROPOSED event) in recorded order."""
        return [(e.action_id, e.seq) for e in self._monitor.find_events(
            workflow_id=workflow_id, event_type=EventType.ACTION_PROPOSED) if e.action_id]

    def proposal_seq(self, action_id: str) -> int:
        events = self._monitor.find_events(action_id=action_id, event_type=EventType.ACTION_PROPOSED)
        if not events:
            raise ObservationError(f"no ACTION_PROPOSED event for {action_id}")
        return events[0].seq

    # ------------------------------------------------------------------ snapshot

    def snapshot(self, workflow_id: str, cutoff_seq: int | None = None) -> WorkflowSnapshot:
        workflow = self._monitor.get_workflow(workflow_id)
        if workflow is None:
            raise ObservationError(f"unknown workflow {workflow_id}")
        all_events = self._monitor.get_workflow_history(workflow_id)
        if cutoff_seq is None:
            cutoff_seq = all_events[-1].seq if all_events else 0
        events = [self._sanitize(e) for e in all_events if e.seq <= cutoff_seq]
        cutoff_time = events[-1].created_at if events else None

        actions: list[ActionRequest] = []
        proposed_at: dict[str, int] = {}
        for e in events:
            if e.event_type is EventType.ACTION_PROPOSED and e.action_id:
                action = self._monitor.get_action(e.action_id)
                if action is not None:
                    actions.append(action)
                    proposed_at[action.action_id] = e.seq

        approvals = self._approvals_as_of(workflow_id, events, cutoff_time)

        refs: set[tuple[str, int]] = set()
        for e in events:
            if e.resource_id:
                for v in (e.resource_version_before, e.resource_version_after):
                    if v:
                        refs.add((e.resource_id, v))
        for a in actions:
            if a.resource_id:
                # An unversioned target resolves to the version current when *that action* was proposed,
                # never to a later version the workflow did not observe.
                v = a.resource_version or self._latest_version_as_of(a.resource_id, proposed_at[a.action_id])
                if v:
                    refs.add((a.resource_id, v))
        for ap in approvals:
            refs.add((ap.resource_id, ap.resource_version))
        resources = self._resolve_versions(refs, cutoff_seq)

        return WorkflowSnapshot(
            workflow_id=workflow_id, cutoff_seq=cutoff_seq, cutoff_time=cutoff_time,
            participants=list(workflow.participants), workflow_purpose=workflow.metadata.get("purpose"),
            events=events, actions=actions, resources=resources, approvals=approvals,
            agent_roles={k: v for k, v in sorted(self._roles.items()) if k in workflow.participants},
        )

    def snapshot_for_action(self, action_id: str) -> WorkflowSnapshot:
        action = self._monitor.get_action(action_id)
        if action is None:
            raise ObservationError(f"unknown action {action_id}")
        return self.snapshot(action.workflow_id, self.proposal_seq(action_id))

    # ------------------------------------------------------------------ windows

    def local(self, snapshot: WorkflowSnapshot, action_id: str) -> MonitorContext:
        """The proposed action, the resource version it targets, and the approval it cites. Nothing else."""
        action, proposal = self._action_in(snapshot, action_id)
        target = self._target(snapshot, action)
        approvals = [a for a in snapshot.approvals if action.approval_id and a.approval_id == action.approval_id]
        return MonitorContext(
            window=ObservationWindow.LOCAL, workflow_id=snapshot.workflow_id, checkpoint_seq=proposal.seq,
            checkpoint_time=proposal.created_at, action=action, agent_role=snapshot.agent_roles.get(action.agent_id),
            events=[proposal], prior_actions=[], resources=[target] if target else [], approvals=approvals,
            workflow_purpose=None, invariants=self._invariants, outside_window=list(LOCAL_EXCLUDED),
            snapshot_digest=snapshot.digest(),
        )

    def global_(self, snapshot: WorkflowSnapshot, action_id: str) -> MonitorContext:
        """All observable workflow history up to and including the action's proposal."""
        action, proposal = self._action_in(snapshot, action_id)
        events = [e for e in snapshot.events if e.seq <= proposal.seq]
        return MonitorContext(
            window=ObservationWindow.GLOBAL, workflow_id=snapshot.workflow_id, checkpoint_seq=proposal.seq,
            checkpoint_time=proposal.created_at, action=action, agent_role=snapshot.agent_roles.get(action.agent_id),
            events=events, prior_actions=[a for a in snapshot.actions if a.action_id != action_id],
            resources=[r for r in snapshot.resources if r.event_seq is None or r.event_seq <= proposal.seq],
            approvals=list(snapshot.approvals), workflow_purpose=snapshot.workflow_purpose,
            invariants=self._invariants, outside_window=list(GLOBAL_EXCLUDED), snapshot_digest=snapshot.digest(),
        )

    def build(self, window: ObservationWindow, snapshot: WorkflowSnapshot, action_id: str) -> MonitorContext:
        return self.local(snapshot, action_id) if window is ObservationWindow.LOCAL else self.global_(snapshot, action_id)

    def build_for(self, *, workflow_id: str, action_id: str,
                  observation_mode: ObservationWindow | str = ObservationWindow.GLOBAL) -> MonitorContext:
        """Convenience: as-of snapshot at the action's proposal, then the requested window."""
        snap = self.snapshot_for_action(action_id)
        if snap.workflow_id != workflow_id:
            raise ObservationError(f"{action_id} does not belong to {workflow_id}")
        return self.build(ObservationWindow(observation_mode), snap, action_id)

    def ethics_context(self, snapshot: WorkflowSnapshot, action_id: str,
                       window: ObservationWindow = ObservationWindow.GLOBAL) -> WorkflowContext:
        """Stage 3: the ``WorkflowContext`` handed to every ethics monitor in a paired comparison.

        It carries the Stage 2 as-of observation (``observation``) plus its history and prior actions.
        The mutable current-state fields (``resource``, ``approvals``) are deliberately left empty, so
        no later status can leak; the approval states as of the checkpoint are in ``observation``.
        """
        obs = self.build(window, snapshot, action_id)
        return WorkflowContext(
            workflow_id=snapshot.workflow_id, action=obs.action, history=list(obs.events),
            related_actions=list(obs.prior_actions), resource=None, resource_history=[], resource_versions=[],
            approvals=[], scope="FULL_WORKFLOW" if window is ObservationWindow.GLOBAL else "ACTION_ONLY",
            observation=obs,
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _sanitize(e: WorkflowEvent) -> WorkflowEvent:
        # Workflow titles/objectives are scenario-author metadata, not observed behavior.
        if e.event_type is EventType.WORKFLOW_STARTED:
            return e.model_copy(update={"summary": NEUTRAL_START_SUMMARY})
        return e

    @staticmethod
    def _action_in(snapshot: WorkflowSnapshot, action_id: str) -> tuple[ActionRequest, WorkflowEvent]:
        action = next((a for a in snapshot.actions if a.action_id == action_id), None)
        proposal = next((e for e in snapshot.events
                         if e.action_id == action_id and e.event_type is EventType.ACTION_PROPOSED), None)
        if action is None or proposal is None:
            raise ObservationError(f"action {action_id} is not observable in this snapshot (cutoff "
                                   f"{snapshot.cutoff_seq})")
        return action, proposal

    @staticmethod
    def _target(snapshot: WorkflowSnapshot, action: ActionRequest) -> ResourceSnapshot | None:
        if not action.resource_id:
            return None
        versions = [r for r in snapshot.resources if r.resource_id == action.resource_id]
        if action.resource_version:
            return next((r for r in versions if r.version == action.resource_version), None)
        return max(versions, key=lambda r: r.version, default=None)

    def _event_seq(self, event_id: str | None) -> int | None:
        if event_id is None:
            return None
        if event_id not in self._seq_cache:
            event = self._monitor.get_event(event_id)
            self._seq_cache[event_id] = event.seq if event else None
        return self._seq_cache[event_id]

    def _latest_version_as_of(self, resource_id: str, cutoff_seq: int) -> int | None:
        best = None
        for v in self._repos.resources.versions(resource_id):
            seq = self._event_seq(v.event_id)
            if seq is not None and seq <= cutoff_seq:
                best = v.version
        return best

    def _resolve_versions(self, refs: set[tuple[str, int]], cutoff_seq: int) -> list[ResourceSnapshot]:
        out: list[ResourceSnapshot] = []
        by_resource: dict[str, set[int]] = {}
        for rid, v in refs:
            by_resource.setdefault(rid, set()).add(v)
        for rid in sorted(by_resource):
            current = self._repos.resources.get(rid)
            if current is None:
                continue
            for snap in self._repos.resources.versions(rid):
                if snap.version not in by_resource[rid]:
                    continue
                seq = self._event_seq(snap.event_id)
                if seq is None or seq > cutoff_seq:
                    continue  # created after the checkpoint: not observable yet
                out.append(ResourceSnapshot(
                    resource_id=rid, resource_type=current.resource_type, version=snap.version,
                    content=dict(snap.content), agent_id=snap.agent_id, action_id=snap.action_id,
                    event_id=snap.event_id, event_seq=seq,
                ))
        return sorted(out, key=lambda r: (r.event_seq or 0, r.resource_id, r.version))

    def _approvals_as_of(self, workflow_id: str, events: list[WorkflowEvent],
                         cutoff_time: datetime | None) -> list[ApprovalView]:
        views: list[ApprovalView] = []
        for record in self._repos.approvals.list(workflow_id=workflow_id):
            trail = [e for e in events if e.approval_id == record.approval_id and e.event_type in _APPROVAL_TRANSITIONS]
            if not trail or trail[0].event_type is not EventType.APPROVAL_REQUESTED:
                continue  # not yet requested at this checkpoint
            status, approver, expires = ApprovalStatus.PENDING, None, None
            for e in trail:
                status = _APPROVAL_TRANSITIONS[e.event_type]
                if e.event_type in (EventType.APPROVAL_GRANTED, EventType.APPROVAL_REJECTED):
                    approver = e.agent_id
                if e.event_type is EventType.APPROVAL_GRANTED and e.details.get("expires_at"):
                    expires = datetime.fromisoformat(e.details["expires_at"])
            if status is ApprovalStatus.APPROVED and expires and cutoff_time and expires <= cutoff_time:
                status = ApprovalStatus.EXPIRED
            views.append(ApprovalView(
                approval_id=record.approval_id, operation=record.operation, resource_id=record.resource_id,
                resource_version=record.resource_version, approved_parameters=dict(record.approved_parameters),
                requested_by=record.requested_by, requesting_action_id=record.requesting_action_id, status=status,
                approver_id=approver, expires_at=expires, evidence_event_ids=[e.event_id for e in trail],
            ))
        return views
