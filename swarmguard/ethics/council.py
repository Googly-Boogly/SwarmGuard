"""Ethics council: runs monitors independently and aggregates advisory output.

The council never authorizes or executes anything. Its report is passed to the
``DecisionEngine``, which may only make a deterministic ALLOW stricter.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from swarmguard.clock import Clock, SystemClock
from swarmguard.ethics.base import EthicsMonitor
from swarmguard.ethics.care import MockCareEthicsMonitor
from swarmguard.ethics.consequentialism import MockConsequentialistMonitor
from swarmguard.ethics.deontology import MockDeontologicalMonitor
from swarmguard.ethics.virtue import MockVirtueEthicsMonitor
from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import (
    CouncilReport,
    EthicalAssessment,
    EthicalFramework,
    FrameworkPosition,
    MonitorFailure,
    Recommendation,
    WorkflowContext,
)

logger = logging.getLogger("swarmguard.council")

REQUIRED_FRAMEWORKS: tuple[EthicalFramework, ...] = tuple(EthicalFramework)
AGGREGATION_RULE = (
    "unanimity: ALLOW only if every required framework returned ALLOW; BLOCK only if every required "
    "framework returned BLOCK; any disagreement, failed monitor, or missing framework -> ESCALATE"
)


class EthicsCouncil:
    def __init__(
        self,
        monitors: Sequence[EthicsMonitor],
        *,
        timeout_seconds: float = 30.0,
        required_frameworks: Sequence[EthicalFramework] = REQUIRED_FRAMEWORKS,
        clock: Clock | None = None,
    ) -> None:
        self._monitors = list(monitors)
        self._timeout = timeout_seconds
        self._required = tuple(required_frameworks)
        self._clock = clock or SystemClock()

    @property
    def monitors(self) -> list[EthicsMonitor]:
        return list(self._monitors)

    async def deliberate(self, action: ActionRequest, context: WorkflowContext) -> CouncilReport:
        results = await asyncio.gather(
            *(self._run_one(m, action, context) for m in self._monitors), return_exceptions=False
        )
        assessments: list[EthicalAssessment] = []
        failures: list[MonitorFailure] = []
        for item in results:
            (assessments if isinstance(item, EthicalAssessment) else failures).append(item)  # type: ignore[arg-type]

        present = {a.framework for a in assessments}
        missing = [f for f in self._required if f not in present]
        positions = [
            FrameworkPosition(framework=a.framework, recommendation=a.recommendation, risk_level=a.risk_level)
            for a in assessments
        ]
        recommendations = {a.recommendation for a in assessments}
        disagreement = len(recommendations) > 1

        complete = not failures and not missing
        if complete and recommendations == {Recommendation.ALLOW}:
            overall = Recommendation.ALLOW
        elif complete and recommendations == {Recommendation.BLOCK}:
            overall = Recommendation.BLOCK
        else:
            overall = Recommendation.ESCALATE  # disagreement or incompleteness goes to a human

        parts = [f"{p.framework.value}={p.recommendation.value}/{p.risk_level.value}" for p in positions]
        summary = f"Advisory {overall.value} (unanimity rule). Positions: {', '.join(parts) or 'none'}."
        if disagreement:
            summary += " Monitors disagree; all positions preserved."
        if failures:
            summary += f" Failed monitors: {', '.join(f.monitor_id for f in failures)}."
        if missing:
            summary += f" Missing frameworks: {', '.join(m.value for m in missing)}."
        contains_mock = any(a.is_mock for a in assessments)
        if contains_mock:
            summary += " Contains MOCK (non-LLM) assessments."

        return CouncilReport(
            action_id=action.action_id,
            assessments=assessments,
            failures=failures,
            missing_frameworks=missing,
            positions=positions,
            disagreement_detected=disagreement,
            council_recommendation=overall,
            evidence_event_ids=sorted({e for a in assessments for e in a.evidence_event_ids}),
            aggregation_rule=AGGREGATION_RULE,
            summary=summary,
            contains_mock_assessments=contains_mock,
            created_at=self._clock.now(),
        )

    async def _run_one(
        self, monitor: EthicsMonitor, action: ActionRequest, context: WorkflowContext
    ) -> EthicalAssessment | MonitorFailure:
        monitor_id = getattr(monitor, "monitor_id", type(monitor).__name__)
        try:
            result = await asyncio.wait_for(monitor.evaluate(action, context), timeout=self._timeout)
            assessment = EthicalAssessment.model_validate(result)
            if assessment.framework is not monitor.framework:
                raise ValueError(f"monitor returned framework {assessment.framework.value}")
            if assessment.action_id != action.action_id:
                raise ValueError("assessment refers to a different action")
            return assessment
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, never fatal
            logger.warning("ethics monitor %s failed: %s", monitor_id, exc)
            return MonitorFailure(
                framework=monitor.framework,
                monitor_id=monitor_id,
                error_type=type(exc).__name__,
                message=str(exc)[:500],
            )


def build_mock_council(clock: Clock | None = None) -> EthicsCouncil:
    """Four clearly labeled MOCK monitors (deterministic heuristics, no LLM)."""
    return EthicsCouncil(
        [
            MockVirtueEthicsMonitor(),
            MockConsequentialistMonitor(),
            MockDeontologicalMonitor(),
            MockCareEthicsMonitor(),
        ],
        clock=clock,
    )
