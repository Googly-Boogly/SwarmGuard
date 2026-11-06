"""Combine the deterministic policy decision with the advisory ethics council.

Rules (monotone: advice can only make a decision *stricter*, never looser):

1. A policy BLOCK is final. Nothing overrides it.
2. A policy ESCALATE stays ESCALATE (a human must act).
3. A policy ALLOW becomes ESCALATE if ``escalate_on_advisory_concern`` and the
   council recommends BLOCK or ESCALATE. Advice never becomes a BLOCK by
   itself: ethics monitors are advisory and fallible, so a concern routes the
   action to a human instead.
4. Council failures do not change ALLOW unless ``escalate_on_council_failure``.
"""

from __future__ import annotations

from dataclasses import dataclass

from swarmguard.models.decisions import Decision, PolicyDecision, ReasonCode
from swarmguard.models.assessments import CouncilReport, Recommendation


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

    def combine(self, policy: PolicyDecision, council: CouncilReport | None) -> CombinedDecision:
        advisory = Decision(council.council_recommendation.value) if council else None
        summary = council.summary if council else None

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
