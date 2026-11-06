"""Experiment runner. Each scenario runs in a fresh, isolated, in-memory simulator.

Stage 2: ``run_stage2_experiment`` (bottom of this module) runs the paired local/global
monitor comparison plus enforcement-mode prevention runs.

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


# =========================================================================== Stage 2 paired evaluation
#
# For every scenario:
#   1. a fresh in-memory simulator (deterministic Stage 1 policy, no guard, no council)
#   2. run the scripted multi-agent workflow (gateway decisions are real)
#   3. for every action checkpoint, build ONE as-of snapshot (no later events) and derive
#      the LOCAL and GLOBAL observations from it; evaluate each monitor independently
#   4. verify the monitors did not mutate the simulation (audit-log fingerprint)
#   5. only then read the scenario's ground truth and join it to the predictions
#   6. separately re-run the scenario under each enforcement configuration (prevention)


import platform  # noqa: E402

from swarmguard import __version__  # noqa: E402
from swarmguard.clock import Clock, SystemClock  # noqa: E402
from swarmguard.evaluation.datasets import DatasetItem, build_dataset, composition  # noqa: E402
from swarmguard.evaluation.experiment import (  # noqa: E402
    ENFORCEMENT_CONFIGURATIONS,
    MONITOR_CONFIGURATIONS,
    Stage2ExperimentConfig,
)
from swarmguard.evaluation.metrics import (  # noqa: E402
    detection_latency,
    prevention_rate,
    summarize_detection,
)
from swarmguard.evaluation.results import (  # noqa: E402
    CheckpointRecord,
    EnforcementOutcome,
    ExperimentResults,
    ScenarioPrediction,
    code_revision,
)
from swarmguard.evaluation.scenarios.base import ScenarioStepFailed  # noqa: E402
from swarmguard.models.decisions import ReasonCode  # noqa: E402
from swarmguard.monitoring.assessment import SafetyLabel, is_alert, scenario_prediction  # noqa: E402
from swarmguard.monitoring.base import SafetyMonitor  # noqa: E402
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor  # noqa: E402
from swarmguard.monitoring.local_monitor import LocalActionMonitor  # noqa: E402
from swarmguard.models.risk import ObservationWindow, SafetyAssessment  # noqa: E402


class MonitorSideEffectError(RuntimeError):
    """Monitor evaluation changed the simulation state (would invalidate the comparison)."""


def _fingerprint(sim: SwarmSimulator, workflow_id: str) -> tuple:
    return (
        tuple(e.event_id for e in sim.monitor.get_workflow_history(workflow_id)),
        tuple(a.action_id for a in sim.monitor.get_workflow_actions(workflow_id)),
        tuple((d.decision_id, d.decision.value) for d in sim.monitor.get_decisions(workflow_id)),
        tuple((a.approval_id, a.status.value) for a in sim.approvals.list_for_workflow(workflow_id)),
        len(sim.sent_emails()),
    )


def default_monitors() -> dict[str, SafetyMonitor]:
    return {"local_action": LocalActionMonitor(), "global_workflow": GlobalWorkflowMonitor()}


async def observe_scenario(item: DatasetItem, monitors: dict[str, SafetyMonitor]
                           ) -> tuple[SwarmSimulator, str, dict[str, list[tuple[int, SafetyAssessment]]], list[str]]:
    """Run one scenario and evaluate every monitor at every checkpoint. Ground truth is NOT read here."""
    sim = SwarmSimulator(":memory:", clock=SteppingClock())
    run = await item.scenario.run(sim, item.workflow_id)
    builder = sim.observation_builder()
    before = _fingerprint(sim, item.workflow_id)
    per_config: dict[str, list[tuple[int, SafetyAssessment]]] = {name: [] for name in monitors}
    order: list[str] = []
    for step, (action_id, seq) in enumerate(builder.checkpoints(item.workflow_id), 1):
        snapshot = builder.snapshot(item.workflow_id, seq)
        order.append(action_id)
        digests = set()
        for name, monitor in monitors.items():
            ctx = builder.build(monitor.window, snapshot, action_id)
            assessment = await monitor.evaluate(ctx.action, ctx)
            digests.add(assessment.snapshot_digest)
            per_config[name].append((step, assessment))
        if len(digests) != 1:
            raise RuntimeError(f"paired monitors evaluated different snapshots at {action_id}")
    if _fingerprint(sim, item.workflow_id) != before:
        raise MonitorSideEffectError(f"monitor evaluation mutated workflow {item.workflow_id}")
    assert run.critical_action_id is not None
    return sim, run.critical_action_id, per_config, order


def _checkpoint_record(scenario_id: str, wf: str, cfg: str, step: int, a: SafetyAssessment,
                       sim: SwarmSimulator) -> CheckpointRecord:
    action = sim.monitor.get_action(a.action_id)
    assert action is not None
    return CheckpointRecord(
        scenario_id=scenario_id, workflow_id=wf, configuration=cfg, step=step, action_id=a.action_id,
        tool=action.tool, agent_id=action.agent_id, checkpoint_seq=a.checkpoint_seq,
        snapshot_digest=a.snapshot_digest, recommendation=a.recommendation.value, risk_level=a.risk_level.value,
        uncertainty=a.uncertainty.value, alert=is_alert(a), detected_patterns=[p.value for p in a.detected_patterns],
        evidence_event_ids=a.evidence_event_ids, missing_information=a.missing_information, explanation=a.explanation,
    )


async def _enforcement_run(item: DatasetItem, cfg: str, window: ObservationWindow | None) -> EnforcementOutcome:
    sim = SwarmSimulator(":memory:", clock=SteppingClock(), workflow_enforcement=window)
    try:
        try:
            run = await item.scenario.run(sim, item.workflow_id)
            critical = sim.monitor.get_decision_for_action(run.critical_action_id or "")
            crit_decision = critical.decision.value if critical else "UNDECIDED"
            crit_reason = critical.reason_code.value if critical else ""
        except ScenarioStepFailed as exc:  # recorded, never hidden
            crit_decision, crit_reason = "SCENARIO_ABORTED", str(exc)[:200]
        sends = [d for d in sim.monitor.get_decisions(item.workflow_id) if d.tool == "send_email"]
        gt = item.scenario.ground_truth()
        occurred = gt.prohibited_outcome.occurred(sim.sent_emails(item.workflow_id)) if gt.prohibited_outcome else None
        return EnforcementOutcome(
            scenario_id=item.scenario.scenario_id, configuration=cfg, ground_truth=gt.label.value,
            critical_decision=crit_decision, critical_reason_code=crit_reason,
            escalated_by_workflow_guard=sum(d.reason_code is ReasonCode.WORKFLOW_RISK_ESCALATION for d in sends),
            blocked_by_policy=sum(d.policy_decision is not Decision.ALLOW for d in sends),
            simulated_emails_sent=len(sim.sent_emails(item.workflow_id)), prohibited_outcome_occurred=occurred,
            prevented=(not occurred) if gt.expected_unsafe and occurred is not None else None,
        )
    finally:
        sim.close()


def _metrics(predictions: list[ScenarioPrediction], checkpoints: list[CheckpointRecord],
             enforcement: list[EnforcementOutcome], configs: list[str]) -> dict:
    detection: dict = {}
    for cfg in configs:
        rows = [p for p in predictions if p.configuration == cfg and p.ground_truth in ("SAFE", "UNSAFE")]
        matrix = ConfusionMatrix.from_pairs((p.ground_truth == "UNSAFE", p.prediction == "UNSAFE") for p in rows)
        critical = ConfusionMatrix.from_pairs((p.ground_truth == "UNSAFE", p.detected_at_critical_step) for p in rows)
        unsafe = [p for p in rows if p.ground_truth == "UNSAFE"]
        safe_ids = {p.scenario_id for p in rows if p.ground_truth == "SAFE"}
        safe_cps = [c for c in checkpoints if c.configuration == cfg and c.scenario_id in safe_ids]
        detection[cfg] = {
            **summarize_detection(matrix),
            "predicted_insufficient_information": sum(p.prediction == "INSUFFICIENT_INFORMATION" for p in rows),
            "latency": detection_latency([p.first_detection_step for p in unsafe], [p.critical_step for p in unsafe]),
            "at_critical_checkpoint": summarize_detection(critical),
            "safe_checkpoint_alerts": sum(c.alert for c in safe_cps),
            "safe_checkpoints": len(safe_cps),
        }
    paired: dict = {}
    if {"local_action", "global_workflow"} <= set(configs):
        by = {(p.scenario_id, p.configuration): p for p in predictions}
        ids = sorted({p.scenario_id for p in predictions})
        for label in ("UNSAFE", "SAFE"):
            sel = [i for i in ids if by[(i, "global_workflow")].ground_truth == label]
            flags = [(by[(i, "local_action")].prediction == "UNSAFE", by[(i, "global_workflow")].prediction == "UNSAFE")
                     for i in sel]
            paired[label.lower()] = {"n": len(sel), "both_flagged": sum(a and b for a, b in flags),
                                     "only_local": sum(a and not b for a, b in flags),
                                     "only_global": sum(b and not a for a, b in flags),
                                     "neither": sum(not a and not b for a, b in flags)}
    prevention: dict = {}
    for cfg in sorted({e.configuration for e in enforcement}):
        rows = [e for e in enforcement if e.configuration == cfg]
        unsafe = [e for e in rows if e.ground_truth == "UNSAFE" and e.prohibited_outcome_occurred is not None]
        safe = [e for e in rows if e.ground_truth == "SAFE"]
        prevention[cfg] = {
            "unsafe_scenarios": len(unsafe),
            "prevention_rate": prevention_rate(e.prohibited_outcome_occurred for e in unsafe),
            "prevented": sum(bool(e.prevented) for e in unsafe),
            "prevented_with_policy_block": sum(bool(e.prevented) and e.blocked_by_policy > 0 for e in unsafe),
            "prevented_with_guard_escalation_only": sum(bool(e.prevented) and e.blocked_by_policy == 0
                                                        and e.escalated_by_workflow_guard > 0 for e in unsafe),
            "safe_scenarios": len(safe),
            "safe_scenarios_with_guard_escalation": sum(e.escalated_by_workflow_guard > 0 for e in safe),
            "safe_scenarios_completed": sum(e.critical_decision == "ALLOW" for e in safe),
        }
    return {"detection": detection, "paired": paired, "prevention": prevention}


async def run_stage2_experiment(config: Stage2ExperimentConfig, *, monitors: dict[str, SafetyMonitor] | None = None,
                                clock: Clock | None = None, revision: str | None = None) -> ExperimentResults:
    """``revision``: caller-verified code revision (e.g. ``git describe --always --dirty``). If omitted,
    the HEAD commit is read from ``.git`` and labelled as possibly not matching uncommitted changes."""
    started = (clock or SystemClock()).now()
    unknown = (set(config.monitor_configurations) - set(MONITOR_CONFIGURATIONS)) | (
        set(config.enforcement_configurations) - set(ENFORCEMENT_CONFIGURATIONS))
    if unknown:
        raise ValueError(f"unknown configurations: {sorted(unknown)}")
    all_monitors = monitors or default_monitors()
    active = {k: all_monitors[k] for k in config.monitor_configurations}
    items = build_dataset(config.seed, config.scenario_ids)

    predictions: list[ScenarioPrediction] = []
    checkpoints: list[CheckpointRecord] = []
    enforcement: list[EnforcementOutcome] = []
    truths: list[dict] = []
    for item in items:
        sid, wf = item.scenario.scenario_id, item.workflow_id
        sim, critical_id, per_config, order = await observe_scenario(item, active)
        try:
            decision = sim.monitor.get_decision_for_action(critical_id)
            critical_step = order.index(critical_id) + 1
            pending: list[ScenarioPrediction] = []
            for cfg, assessed in per_config.items():
                checkpoints.extend(_checkpoint_record(sid, wf, cfg, step, a, sim) for step, a in assessed)
                alerts = [(step, a) for step, a in assessed if is_alert(a)]
                first = alerts[0] if alerts else None
                at_critical = next(a for step, a in assessed if step == critical_step)
                pending.append(ScenarioPrediction(
                    experiment_id=config.experiment_id, scenario_id=sid, workflow_id=wf, configuration=cfg,
                    prediction=scenario_prediction(a for _, a in assessed).value, detected=bool(alerts),
                    first_detection_step=first[0] if first else None,
                    first_detection_action_id=first[1].action_id if first else None, critical_step=critical_step,
                    detected_at_critical_step=is_alert(at_critical), alert_checkpoints=len(alerts),
                    checkpoints=len(assessed),
                    detected_patterns=sorted({p.value for _, a in alerts for p in a.detected_patterns}),
                    evidence_event_ids=(first[1] if first else at_critical).evidence_event_ids,
                    authorization_result=decision.decision.value if decision else "UNDECIDED",
                    authorization_reason_code=decision.reason_code.value if decision else "",
                    prohibited_outcome_occurred=None,
                ))
            # Ground truth is read only now, after every prediction for this scenario is fixed.
            gt = item.scenario.ground_truth()
            occurred = gt.prohibited_outcome.occurred(sim.sent_emails(wf)) if gt.prohibited_outcome else None
            truths.append({**gt.model_dump(mode="json"), "workflow_id": wf})
            for p in pending:
                predictions.append(p.model_copy(update={
                    "ground_truth": gt.label.value, "safety_property": gt.safety_property.value,
                    "prohibited_outcome_occurred": occurred,
                    "correct": (p.prediction == SafetyLabel.UNSAFE.value) == gt.expected_unsafe}))
            if "deterministic_only" in config.enforcement_configurations:
                sends = [d for d in sim.monitor.get_decisions(wf) if d.tool == "send_email"]
                enforcement.append(EnforcementOutcome(
                    scenario_id=sid, configuration="deterministic_only", ground_truth=gt.label.value,
                    critical_decision=decision.decision.value if decision else "UNDECIDED",
                    critical_reason_code=decision.reason_code.value if decision else "",
                    escalated_by_workflow_guard=0, blocked_by_policy=sum(d.policy_decision is not Decision.ALLOW
                                                                         for d in sends),
                    simulated_emails_sent=len(sim.sent_emails(wf)), prohibited_outcome_occurred=occurred,
                    prevented=(not occurred) if gt.expected_unsafe and occurred is not None else None))
        finally:
            sim.close()
        for cfg in config.enforcement_configurations:
            if cfg != "deterministic_only":
                enforcement.append(await _enforcement_run(item, cfg, ENFORCEMENT_CONFIGURATIONS[cfg]))

    metrics = _metrics(predictions, checkpoints, enforcement, list(active))
    metrics["dataset"] = composition(items)
    metadata = {
        "executed_at": started.isoformat(), "swarmguard_version": __version__,
        "ruleset_version": config.ruleset_version,
        "monitor_implementations": {k: f"{type(m).__name__}/{config.ruleset_version}" for k, m in active.items()},
        "scenario_versions": {i.scenario.scenario_id: i.scenario.ground_truth().scenario_version for i in items},
        "code_revision": revision or (f"{head} (HEAD; uncommitted changes not checked)" if (head := code_revision())
                                      else "unavailable (no git repository found)"),
        "random_seed": config.seed, "scenario_count": len(items), "python": platform.python_version(),
        "positive_mapping": config.positive_mapping,
        "monitor_method": "deterministic rules (no LLM, no network, no API keys)",
    }
    return ExperimentResults(config=config.model_dump(mode="json"), metadata=metadata, predictions=predictions,
                             checkpoints=checkpoints, enforcement=enforcement, ground_truth=truths, metrics=metrics)
