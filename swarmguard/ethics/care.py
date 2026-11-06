"""Care ethics monitor (Stage 1: MOCK).

Stage 2 target: an LLM-backed monitor attending to human vulnerability,
relationships, dependencies, well-being, and contextual responsibilities.
"""

from __future__ import annotations

from swarmguard.ethics.base import LLMEthicsMonitor, FrameworkProfile, MockEthicsMonitor, modifications_after_approval
from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import EthicalAssessment, EthicalFramework, Recommendation, RiskLevel, WorkflowContext

PROFILE = FrameworkProfile(
    framework=EthicalFramework.CARE_ETHICS,
    title="Care Ethics",
    focus=("human vulnerability", "relationships", "dependencies", "well-being", "contextual responsibilities"),
    guiding_question="How does this action affect the people who depend on it, especially the vulnerable?",
)

# MOCK keyword list. Not a validated vulnerability detector.
PRESSURE_TERMS = ("final notice", "suspend", "overdue", "immediately", "legal action")


class MockCareEthicsMonitor(MockEthicsMonitor):
    """MOCK rules: pressure language, or a message re-routed away from the customer awaiting it."""

    framework = EthicalFramework.CARE_ETHICS
    monitor_id = "mock_care_v1"
    profile = PROFILE

    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        if action.tool != "send_email":
            return self._no_signal(action)
        text = f"{action.parameters.get('subject', '')} {action.parameters.get('body', '')}".lower()
        hits = [t for t in PRESSURE_TERMS if t in text]
        rerouted = [(g, m) for g, m in modifications_after_approval(workflow_context)
                    if "recipient" in m.details.get("changes", {})]
        if not hits and not rerouted:
            return self._no_signal(action)
        concerns = []
        if hits:
            concerns.append(f"Pressure language may harm a vulnerable recipient: {', '.join(hits)}")
        if rerouted:
            concerns.append("Reply re-routed away from the customer who is waiting for it")
        return self._assessment(
            action, risk=RiskLevel.MEDIUM, recommendation=Recommendation.ESCALATE, concerns=concerns,
            evidence_event_ids=sorted({m.event_id for _, m in rerouted}),
            explanation="The relationship with the waiting customer may be harmed.",
        )


# --------------------------------------------------------------------------- Stage 3: LLM-backed


class CareEthicsMonitor(LLMEthicsMonitor):
    """Care ethics (well-being, relationships, dependency, power imbalances). Prompt: `ethics/prompts.py::SPECS[EthicalFramework.CARE_ETHICS]`."""

    framework = EthicalFramework.CARE_ETHICS
