"""Persisted sequence is chronology; only parent_event_ids establish causality."""
from __future__ import annotations
from swarmguard.models.events import WorkflowEvent


class TemporalAnalysis:
    def __init__(self, events: list[WorkflowEvent]) -> None:
        self.events = sorted(events, key=lambda e: e.seq)
        self.by_id = {e.event_id: e for e in self.events}

    def get_preceding_events(self, event_id: str) -> list[WorkflowEvent]:
        return [e for e in self.events if e.seq < self.by_id[event_id].seq]

    def get_following_events(self, event_id: str) -> list[WorkflowEvent]:
        return [e for e in self.events if e.seq > self.by_id[event_id].seq]

    def dependencies(self, event_id: str) -> list[WorkflowEvent]:
        seen: set[str] = set()
        stack = list(self.by_id[event_id].parent_event_ids)
        while stack:
            parent = stack.pop()
            if parent not in seen and parent in self.by_id:
                seen.add(parent)
                stack.extend(self.by_id[parent].parent_event_ids)
        return [e for e in self.events if e.event_id in seen]

    def get_first_risk_detection(self, workflow_id: str) -> WorkflowEvent | None:
        return next((e for e in self.events if e.workflow_id == workflow_id and e.event_type.value == "WORKFLOW_RISK_ASSESSMENT"
                     and e.decision in ("BLOCK", "ESCALATE")), None)

    def get_first_prohibited_proposal(self, workflow_id: str, verified_action_ids: set[str]) -> WorkflowEvent | None:
        return self._first(workflow_id, "ACTION_PROPOSED", verified_action_ids)

    def get_first_prohibited_execution(self, workflow_id: str, verified_action_ids: set[str]) -> WorkflowEvent | None:
        return self._first(workflow_id, "ACTION_EXECUTED", verified_action_ids)

    def _first(self, wid: str, kind: str, ids: set[str]) -> WorkflowEvent | None:
        return next((e for e in self.events if e.workflow_id == wid and e.event_type.value == kind and e.action_id in ids), None)
