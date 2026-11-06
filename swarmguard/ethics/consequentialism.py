"""Consequentialist monitor (Stage 1: MOCK).

Stage 2 target: an LLM-backed monitor weighing potential benefits, potential
harms, downstream consequences, and stakeholder outcomes.
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
    framework=EthicalFramework.CONSEQUENTIALISM,
    title="Consequentialism",
    focus=("potential benefits", "potential harms", "downstream consequences", "stakeholder outcomes"),
    guiding_question="Which outcomes does this action make more likely, and for whom?",
)


class MockConsequentialistMonitor(MockEthicsMonitor):
    """MOCK rule: recipient differs from the one the human approved -> misdirected-disclosure risk."""

    framework = EthicalFramework.CONSEQUENTIALISM
    monitor_id = "mock_consequentialism_v1"
    profile = PROFILE

    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        recipient = action.parameters.get("recipient")
        grants = cited_approval_events(workflow_context, EventType.APPROVAL_GRANTED)
        mismatched = [g for g in grants if g.details.get("approved_parameters", {}).get("recipient") != recipient]
        if action.tool != "send_email" or not mismatched:
            return self._no_signal(action)
        return self._assessment(
            action, risk=RiskLevel.HIGH, recommendation=Recommendation.BLOCK,
            concerns=["Customer information may be disclosed to an unintended recipient"],
            evidence_event_ids=[g.event_id for g in mismatched],
            explanation="The message would reach someone other than the approved recipient.",
            uncertainty=Uncertainty.MEDIUM,
        )


# --------------------------------------------------------------------------- Stage 3: LLM-backed


class ConsequentialistMonitor(LLMEthicsMonitor):
    """Consequentialism (benefits, harms, downstream and cumulative effects, reversibility). Prompt: `ethics/prompts.py::SPECS[EthicalFramework.CONSEQUENTIALISM]`."""

    framework = EthicalFramework.CONSEQUENTIALISM
