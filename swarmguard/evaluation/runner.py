"""Experiment runner. Each scenario runs in a fresh, isolated, in-memory simulator.

Stage 1 can run only the ``deterministic_only`` condition, optionally with the
MOCK council attached. Results are flagged ``is_research_result=False``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.ethics.council import EthicsCouncil
from swarmguard.evaluation.baselines import condition as get_condition
from swarmguard.evaluation.metrics import ConfusionMatrix, summarize
from swarmguard.evaluation.scenarios import DEMO_SCENARIOS, ScenarioDefinition
from swarmguard.models.decisions import Decision


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    condition_id: str = "deterministic_only"
    scenario_ids: list[str] = Field(default_factory=lambda: [s.scenario_id for s in DEMO_SCENARIOS])
    use_mock_council: bool = False


class MonitorPrediction(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    condition_id: str
    ground_truth_harmful: bool
    flagged: bool
    decision: Decision
    reason_code: str
    advisory_recommendation: str | None = None
    matched_expected_decision: bool


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: ExperimentConfig
    predictions: list[MonitorPrediction]
    metrics: dict[str, float | int | None]
    is_research_result: bool = False
    note: str = "Stage 1 smoke evaluation on two scripted demo scenarios; not evidence for RQ1-RQ3."


async def run_experiment(config: ExperimentConfig,
                         scenarios: Sequence[ScenarioDefinition] = DEMO_SCENARIOS,
                         council_factory: Callable[[], EthicsCouncil] | None = None) -> EvaluationResult:
    cond = get_condition(config.condition_id)
    if not cond.implemented:
        raise NotImplementedError(f"condition '{cond.condition_id}' requires Stage 2 monitors")
    if config.use_mock_council and council_factory is None:
        from swarmguard.ethics.council import build_mock_council
        council_factory = build_mock_council
    predictions = []
    for spec in (s for s in scenarios if s.scenario_id in config.scenario_ids):
        sim = SwarmSimulator(":memory:", clock=SteppingClock(),
                             council=council_factory() if config.use_mock_council and council_factory else None)
        try:
            outcome = await spec.run(sim, spec.default_workflow_id)
            record = sim.monitor.get_decision_for_action(outcome.final.action_id)
        finally:
            sim.close()
        predictions.append(MonitorPrediction(
            scenario_id=spec.scenario_id, condition_id=cond.condition_id, ground_truth_harmful=spec.label.harmful,
            flagged=outcome.final.decision is not Decision.ALLOW, decision=outcome.final.decision,
            reason_code=outcome.final.reason_code.value,
            advisory_recommendation=record.advisory_recommendation.value if record and record.advisory_recommendation else None,
            matched_expected_decision=outcome.final.decision is spec.label.expected_decision,
        ))
    matrix = ConfusionMatrix.from_pairs((p.ground_truth_harmful, p.flagged) for p in predictions)
    return EvaluationResult(config=config, predictions=predictions, metrics=summarize(matrix))
