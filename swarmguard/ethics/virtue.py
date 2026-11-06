"""Virtue ethics monitor (Stage 1: MOCK).

Stage 2 target: an LLM-backed monitor assessing honesty, integrity,
trustworthiness, fairness, and responsible conduct across the workflow.
"""

from __future__ import annotations

from swarmguard.ethics.base import LLMEthicsMonitor, FrameworkProfile, MockEthicsMonitor, modifications_after_approval
from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import EthicalAssessment, EthicalFramework, Recommendation, RiskLevel, WorkflowContext

PROFILE = FrameworkProfile(
    framework=EthicalFramework.VIRTUE_ETHICS,
    title="Virtue Ethics",
    focus=("honesty", "integrity", "trustworthiness", "fairness", "responsible conduct"),
    guiding_question="Would a person of good character, acting in this role, do this?",
)


class MockVirtueEthicsMonitor(MockEthicsMonitor):
    """MOCK rule: acting under an approval whose content was later altered -> integrity concern."""

    framework = EthicalFramework.VIRTUE_ETHICS
    monitor_id = "mock_virtue_v1"
    profile = PROFILE

    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        pairs = modifications_after_approval(workflow_context)
        if not pairs:
            return self._no_signal(action)
        return self._assessment(
            action, risk=RiskLevel.HIGH, recommendation=Recommendation.ESCALATE,
            concerns=["Potential dishonesty: approved content was altered before execution"],
            evidence_event_ids=sorted({e.event_id for pair in pairs for e in pair}),
            explanation="The proposed operation differs from prior authorization.",
        )


# --------------------------------------------------------------------------- Stage 3: LLM-backed


class VirtueEthicsMonitor(LLMEthicsMonitor):
    """Virtue ethics (honesty, integrity, fairness, trustworthiness, practical wisdom). Prompt: `ethics/prompts.py::SPECS[EthicalFramework.VIRTUE_ETHICS]`."""

    framework = EthicalFramework.VIRTUE_ETHICS
