"""Deontological monitor (Stage 1: MOCK).

Stage 2 target: an LLM-backed monitor assessing duties, obligations, consent,
rights, and moral constraints.
"""

from __future__ import annotations

from swarmguard.ethics.base import LLMEthicsMonitor, FrameworkProfile, MockEthicsMonitor, cited_approval_events
from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import (
    EthicalAssessment,
    EthicalFramework,
    Recommendation,
    RiskLevel,
    Uncertainty,
    WorkflowContext,
)
from swarmguard.models.events import EventType

PROFILE = FrameworkProfile(
    framework=EthicalFramework.DEONTOLOGY,
    title="Deontology",
    focus=("duties", "obligations", "consent", "rights", "moral constraints"),
    guiding_question="Does this action respect the duties owed and the consent actually given?",
)


class MockDeontologicalMonitor(MockEthicsMonitor):
    """MOCK rule: acting on an invalidated approval -> consent no longer covers the action."""

    framework = EthicalFramework.DEONTOLOGY
    monitor_id = "mock_deontology_v1"
    profile = PROFILE

    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        invalidations = cited_approval_events(workflow_context, EventType.APPROVAL_INVALIDATED)
        if not invalidations:
            return self._no_signal(action)
        return self._assessment(
            action, risk=RiskLevel.HIGH, recommendation=Recommendation.BLOCK,
            concerns=["Human consent does not cover the operation as it now stands"],
            evidence_event_ids=[e.event_id for e in invalidations],
            explanation="The cited approval was invalidated before this request.",
            uncertainty=Uncertainty.MEDIUM,
        )


# --------------------------------------------------------------------------- Stage 3: LLM-backed


class DeontologicalMonitor(LLMEthicsMonitor):
    """Deontology (duties, consent, privacy, rights, authorization boundaries). Prompt: `ethics/prompts.py::SPECS[EthicalFramework.DEONTOLOGY]`."""

    framework = EthicalFramework.DEONTOLOGY
