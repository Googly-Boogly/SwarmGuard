"""Common safety-monitor interface (deterministic now, LLM-backed in Stage 3).

A ``SafetyMonitor`` receives an ``ActionRequest`` and a frozen ``MonitorContext`` built by
``ObservationBuilder``, and returns a ``SafetyAssessment``. It holds no database,
repository, workspace, gateway or executor handle. Its output is advisory. It
becomes enforcement only if a trusted ``WorkflowGuard`` is explicitly configured in
the capability gateway, and even then it can only turn ALLOW into ESCALATE.

Stage 3 LLM monitors implement the same interface. They must:
* treat every agent-authored string in the context as data (prompt-injection risk),
* return a schema-valid ``SafetyAssessment`` citing only event IDs from the context,
* never make network calls from trusted code paths without explicit configuration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from swarmguard.models.actions import ActionRequest
from swarmguard.models.risk import MonitorContext, ObservationWindow, SafetyAssessment


class MonitorInputError(ValueError):
    """The monitor received a context outside its defined observation window."""


class SafetyMonitor(ABC):
    monitor_id: str
    window: ObservationWindow

    @abstractmethod
    async def evaluate(self, action: ActionRequest, context: MonitorContext) -> SafetyAssessment:
        """Return a structured, advisory assessment. Must not have side effects."""

    def _validate(self, action: ActionRequest, context: MonitorContext) -> None:
        if not isinstance(context, MonitorContext):
            raise MonitorInputError("context must be a MonitorContext built by ObservationBuilder")
        if context.window is not self.window:
            raise MonitorInputError(f"{self.monitor_id} accepts only {self.window.value} observations, "
                                    f"got {context.window.value}")
        if context.action.action_id != action.action_id or context.action != action:
            raise MonitorInputError("context describes a different action")
