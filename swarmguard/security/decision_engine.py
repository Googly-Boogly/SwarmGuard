"""Combine the deterministic policy decision with the advisory ethics council.

Rules (monotone: advice can only make a decision *stricter*, never looser):

1. A policy BLOCK is final. Nothing overrides it.
2. A policy ESCALATE stays ESCALATE (a human must act).
3. A policy ALLOW becomes ESCALATE if ``escalate_on_advisory_concern`` and the
   council recommends BLOCK or ESCALATE. Advice never becomes a BLOCK by
   itself: ethics monitors are advisory and fallible, so a concern routes the
   action to a human instead.
4. Council failures do not change ALLOW unless ``escalate_on_council_failure``.
5. (Stage 2, enforcement mode) A policy ALLOW becomes ESCALATE if a trusted workflow
   guard assessment recommends ESCALATE or BLOCK. Like the council, the guard can only
   route an action to a human. It never blocks by itself and never relaxes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from swarmguard.models.decisions import Decision, PolicyDecision, ReasonCode
from swarmguard.models.assessments import CouncilReport, Recommendation

if TYPE_CHECKING:  # pragma: no cover
    from swarmguard.models.risk import SafetyAssessment


@dataclass(frozen=True)
class CombinedDecision:
    decision: Decision
    reason_code: ReasonCode
    explanation: str
    advisory_recommendation: Decision | None
    advisory_summary: str | None


class DecisionEngine:
    def __init__(
        self,
        *,
        escalate_on_advisory_concern: bool = True,
        escalate_on_council_failure: bool = False,
    ) -> None:
        self.escalate_on_advisory_concern = escalate_on_advisory_concern
        self.escalate_on_council_failure = escalate_on_council_failure

    def combine(self, policy: PolicyDecision, council: CouncilReport | None,
                workflow_risk: SafetyAssessment | None = None) -> CombinedDecision:
        advisory = Decision(council.council_recommendation.value) if council else None
        summary = council.summary if council else None

        if policy.decision is Decision.ALLOW and workflow_risk is not None and workflow_risk.recommendation in (
                Recommendation.BLOCK, Recommendation.ESCALATE):
            patterns = ", ".join(p.value for p in workflow_risk.detected_patterns) or "none"
            return CombinedDecision(
                Decision.ESCALATE,
                ReasonCode.WORKFLOW_RISK_ESCALATION,
                "Deterministic policy allowed the action, but the trusted workflow guard "
                f"({workflow_risk.monitor_id}) recommended {workflow_risk.recommendation.value} "
                f"[{patterns}]; routing to a human.",
                advisory,
                summary,
            )

        if policy.decision is not Decision.ALLOW or council is None:
            return CombinedDecision(
                policy.decision, policy.reason_code, policy.explanation, advisory, summary
            )

        concern = council.council_recommendation in (Recommendation.BLOCK, Recommendation.ESCALATE)
        incomplete = bool(council.failures or council.missing_frameworks)
        if (concern and self.escalate_on_advisory_concern) or (
            incomplete and self.escalate_on_council_failure
        ):
            return CombinedDecision(
                Decision.ESCALATE,
                ReasonCode.ETHICS_ESCALATION,
                "Deterministic policy allowed the action, but the advisory ethics council "
                f"recommended {council.council_recommendation.value}; routing to a human.",
                advisory,
                summary,
            )
        return CombinedDecision(policy.decision, policy.reason_code, policy.explanation, advisory, summary)
