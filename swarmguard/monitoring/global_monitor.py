"""Global Workflow Monitor: the same rules, applied to the observable workflow up to the checkpoint.

It receives ``ObservationBuilder.global_`` contexts: every event of the workflow with
``seq <= checkpoint``, the resource versions those events reference, approval states
replayed up to the checkpoint, prior actions, and the declared workflow purpose.
It never receives future events (the builder enforces the cutoff, and this class
re-checks it).
"""

from __future__ import annotations

from swarmguard.models.actions import ActionRequest
from swarmguard.models.risk import MonitorContext, ObservationWindow, SafetyAssessment
from swarmguard.monitoring.base import MonitorInputError, SafetyMonitor
from swarmguard.security.risk_analyzer import RiskAnalyzer

GLOBAL_MONITOR_ID = "global_workflow"


class GlobalWorkflowMonitor(SafetyMonitor):
    monitor_id = GLOBAL_MONITOR_ID
    window = ObservationWindow.GLOBAL

    def __init__(self, analyzer: RiskAnalyzer | None = None) -> None:
        self._analyzer = analyzer or RiskAnalyzer()

    async def evaluate(self, action: ActionRequest, context: MonitorContext) -> SafetyAssessment:
        self._validate(action, context)
        if any(e.seq > context.checkpoint_seq for e in context.events) or any(
                (r.event_seq or 0) > context.checkpoint_seq for r in context.resources):
            raise MonitorInputError("global observation contains events after the checkpoint")
        return self._analyzer.assess(context, monitor_id=self.monitor_id)
