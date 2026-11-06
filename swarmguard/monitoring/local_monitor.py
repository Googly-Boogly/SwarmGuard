"""Local Action Monitor: the single-action baseline.

It sees only what ``ObservationBuilder.local`` provides: the proposed action, the
resource version the action targets, and the approval the action cites. It has no
access to workflow history, other resources, provenance or workflow purpose. It runs
the *same* ``RiskAnalyzer`` rules as the Global Workflow Monitor.
"""

from __future__ import annotations

from swarmguard.models.actions import ActionRequest
from swarmguard.models.risk import MonitorContext, ObservationWindow, SafetyAssessment
from swarmguard.monitoring.base import MonitorInputError, SafetyMonitor
from swarmguard.security.risk_analyzer import RiskAnalyzer

LOCAL_MONITOR_ID = "local_action"


class LocalActionMonitor(SafetyMonitor):
    monitor_id = LOCAL_MONITOR_ID
    window = ObservationWindow.LOCAL

    def __init__(self, analyzer: RiskAnalyzer | None = None) -> None:
        self._analyzer = analyzer or RiskAnalyzer()

    async def evaluate(self, action: ActionRequest, context: MonitorContext) -> SafetyAssessment:
        self._validate(action, context)
        if context.prior_actions or any(e.action_id != action.action_id for e in context.events):
            raise MonitorInputError("local observation must not contain workflow history")
        return self._analyzer.assess(context, monitor_id=self.monitor_id)
