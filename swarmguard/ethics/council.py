"""Ethics council: runs monitors independently and aggregates advisory output.

The council never authorizes or executes anything. Its report is passed to the
``DecisionEngine``, which may only make a deterministic ALLOW stricter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from swarmguard.clock import Clock, SystemClock
from swarmguard.ethics.aggregator import AggregationMethod, Aggregator
from swarmguard.ethics.base import EthicsMonitor
from swarmguard.ethics.disagreement import analyze as analyze_disagreement
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
    SPECIALIZED_FRAMEWORKS,
    WorkflowContext,
)

logger = logging.getLogger("swarmguard.council")

REQUIRED_FRAMEWORKS: tuple[EthicalFramework, ...] = SPECIALIZED_FRAMEWORKS
AGGREGATION_RULE = (
    "unanimity: ALLOW only if every required framework returned ALLOW; BLOCK only if every required "
    "framework returned BLOCK; any disagreement, failed monitor, or missing framework -> ESCALATE"
)


class EthicsCouncil:
    """Runs monitors independently and aggregates their advisory output.

    Independence: every monitor receives the same frozen ``WorkflowContext``, and none sees another's
    output. Monitors run concurrently (optionally bounded by ``max_concurrency``) with a per-monitor
    timeout. A failure, a timeout or an invalid output becomes a ``MonitorFailure``: it never fails the
    council and never counts as ALLOW. Individual assessments are stored unmerged.

    Stage 1 behavior (unanimity rule, required four frameworks) remains the default.
    """

    def __init__(
        self,
        monitors: Sequence[EthicsMonitor],
        *,
        timeout_seconds: float = 30.0,
        required_frameworks: Sequence[EthicalFramework] = REQUIRED_FRAMEWORKS,
        clock: Clock | None = None,
        council_type: str = "SPECIALIZED_ETHICS",
        aggregator: Aggregator | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._monitors = list(monitors)
        self._timeout = timeout_seconds
        self._required = tuple(required_frameworks)
        self._clock = clock or SystemClock()
        self.council_type = council_type
        self.aggregator = aggregator or Aggregator(AggregationMethod.UNANIMITY)
        self._sem = asyncio.Semaphore(max_concurrency) if max_concurrency else None

    @property
    def monitors(self) -> list[EthicsMonitor]:
        return list(self._monitors)

    @property
    def monitor_ids(self) -> list[str]:
        return [getattr(m, "monitor_id", type(m).__name__) for m in self._monitors]

    def configuration(self) -> dict[str, object]:
        return {"council_type": self.council_type, "aggregation": self.aggregator.describe(),
                "timeout_seconds": self._timeout, "required_frameworks": [f.value for f in self._required],
                "monitors": [m.configuration() if hasattr(m, "configuration") else
                             {"monitor_id": getattr(m, "monitor_id", type(m).__name__)} for m in self._monitors]}

    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> CouncilReport:
        """Stage 2/3 entry point (same as ``deliberate``). The report remains advisory."""
        return await self.deliberate(action, workflow_context)

    async def deliberate(self, action: ActionRequest, context: WorkflowContext) -> CouncilReport:
        results = await asyncio.gather(*(self._bounded(m, action, context) for m in self._monitors))
        assessments: list[EthicalAssessment] = []
        failures: list[MonitorFailure] = []
        elapsed: dict[str, float] = {}
        for (item, ms), monitor_id in zip(results, self.monitor_ids, strict=True):
            elapsed[monitor_id] = round(ms, 3)
            (assessments if isinstance(item, EthicalAssessment) else failures).append(item)  # type: ignore[arg-type]

        present = {a.framework for a in assessments}
        missing = [f for f in self._required if f not in present]
        positions = [
            FrameworkPosition(framework=a.framework, recommendation=a.recommendation, risk_level=a.risk_level)
            for a in assessments
        ]
        recommendations = {a.recommendation for a in assessments}
        disagreement = len(recommendations) > 1
        outcome = self.aggregator.aggregate([a.recommendation for a in assessments],
                                            incomplete=bool(failures or missing))
        overall = outcome.recommendation

        parts = [f"{p.framework.value}={p.recommendation.value}/{p.risk_level.value}" for p in positions]
        summary = f"Advisory {overall.value} ({self.aggregator.short_name} rule). Positions: {', '.join(parts) or 'none'}."
        if disagreement:
            summary += " Monitors disagree; all positions preserved."
        if failures:
            summary += f" Failed monitors: {', '.join(f.monitor_id for f in failures)}."
        if missing:
            summary += f" Missing frameworks: {', '.join(m.value for m in missing)}."
        contains_mock = any(a.is_mock for a in assessments)
        if contains_mock:
            summary += " Contains MOCK (non-LLM) assessments."
        report = analyze_disagreement(action.action_id, assessments, self.monitor_ids)

        return CouncilReport(
            action_id=action.action_id,
            assessments=assessments,
            failures=failures,
            missing_frameworks=missing,
            positions=positions,
            disagreement_detected=disagreement,
            council_recommendation=overall,
            evidence_event_ids=sorted({e for a in assessments for e in a.evidence_event_ids}),
            aggregation_rule=AGGREGATION_RULE if self.aggregator.method is AggregationMethod.UNANIMITY
            else f"{outcome.method.value}: {self.aggregator.describe()}",
            summary=summary,
            contains_mock_assessments=contains_mock,
            created_at=self._clock.now(),
            council_type=self.council_type,
            workflow_id=action.workflow_id,
            aggregation_method=self.aggregator.method.value,
            failed_monitors=[f.monitor_id for f in failures],
            expected_monitors=self.monitor_ids,
            disagreement=report.model_dump(mode="json"),
            monitor_elapsed_ms=elapsed,
        )

    async def _bounded(self, monitor: EthicsMonitor, action: ActionRequest, context: WorkflowContext
                       ) -> tuple[EthicalAssessment | MonitorFailure, float]:
        start = time.perf_counter()
        if self._sem is None:
            item = await self._run_one(monitor, action, context)
        else:
            async with self._sem:
                item = await self._run_one(monitor, action, context)
        ms = (time.perf_counter() - start) * 1000
        if isinstance(item, MonitorFailure):
            item = item.model_copy(update={"elapsed_ms": round(ms, 3)})
        return item, ms

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
            logger.warning("ethics monitor %s failed: %s", monitor_id, type(exc).__name__)
            kind = getattr(exc, "kind", None) or ("TIMEOUT" if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                                                  else "MONITOR_ERROR")
            result_obj = getattr(exc, "result", None)
            details = getattr(exc, "details", {}) or {}
            return MonitorFailure(
                framework=monitor.framework,
                monitor_id=monitor_id,
                error_type=type(exc).__name__,
                message=str(exc)[:500],
                error_kind=str(kind),
                invalid_evidence_ids=list(details.get("invalid_evidence_ids", [])),
                call=result_obj.model_dump(mode="json", exclude={"response"}) if result_obj is not None else None,
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
