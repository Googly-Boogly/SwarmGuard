"""Ethics-monitor interface.

Every monitor (mock in Stage 1, LLM-backed in Stage 2) implements
``EthicsMonitor.evaluate(action, workflow_context) -> EthicalAssessment`` and
receives the identical ``WorkflowContext``. Monitors are advisory only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import (
    EthicalAssessment,
    EthicalFramework,
    Recommendation,
    RiskLevel,
    Uncertainty,
    WorkflowContext,
)
from swarmguard.models.events import EventType, WorkflowEvent


@dataclass(frozen=True)
class FrameworkProfile:
    """What a framework attends to. Stage 2 will turn this into monitor prompts."""

    framework: EthicalFramework
    title: str
    focus: tuple[str, ...]
    guiding_question: str


class EthicsMonitor(ABC):
    framework: EthicalFramework
    monitor_id: str

    @abstractmethod
    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        """Return a structured, advisory assessment. Must not have side effects."""


class MockEthicsMonitor(EthicsMonitor):
    """Base for Stage 1 placeholders.

    MOCK: these monitors apply a few fixed, hand-written keyword/event rules so
    the council pipeline can be exercised end to end. They perform no ethical
    reasoning, are not LLM outputs, and must not be reported as research
    results.
    """

    profile: FrameworkProfile

    def _assessment(
        self,
        action: ActionRequest,
        *,
        risk: RiskLevel,
        recommendation: Recommendation,
        concerns: list[str],
        evidence_event_ids: list[str],
        explanation: str,
        uncertainty: Uncertainty = Uncertainty.HIGH,
    ) -> EthicalAssessment:
        return EthicalAssessment(
            framework=self.framework,
            action_id=action.action_id,
            risk_level=risk,
            concerns=concerns,
            evidence_event_ids=evidence_event_ids,
            uncertainty=uncertainty,
            recommendation=recommendation,
            explanation=f"[MOCK heuristic, not an LLM assessment] {explanation}",
            monitor_id=self.monitor_id,
            is_mock=True,
        )

    def _no_signal(self, action: ActionRequest) -> EthicalAssessment:
        return self._assessment(
            action,
            risk=RiskLevel.LOW,
            recommendation=Recommendation.ALLOW,
            concerns=[],
            evidence_event_ids=[],
            explanation="No mock heuristic signal matched.",
        )


# --------------------------------------------------------------------------- helpers for mocks


def events_of(context: WorkflowContext, *types: EventType) -> list[WorkflowEvent]:
    return [e for e in context.history if e.event_type in types]


def cited_approval_events(context: WorkflowContext, *types: EventType) -> list[WorkflowEvent]:
    approval_id = context.action.approval_id
    return [e for e in events_of(context, *types) if approval_id and e.approval_id == approval_id]


def modifications_after_approval(context: WorkflowContext) -> list[tuple[WorkflowEvent, WorkflowEvent]]:
    """(APPROVAL_GRANTED, later RESOURCE_MODIFIED) pairs on the resource the cited approval covers.

    Uses explicit version numbers on events, not timestamps: a modification whose
    ``resource_version_before`` is >= the approved version came after the approval.
    """
    pairs = []
    for grant in cited_approval_events(context, EventType.APPROVAL_GRANTED):
        for mod in events_of(context, EventType.RESOURCE_MODIFIED):
            if (mod.resource_id == grant.resource_id and mod.resource_version_before is not None
                    and grant.resource_version_after is not None
                    and mod.resource_version_before >= grant.resource_version_after):
                pairs.append((grant, mod))
    return pairs
