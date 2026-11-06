"""Paired evaluation, metrics and persistence (spec items 25-33, 43)."""

from __future__ import annotations

import csv
import json

import pytest

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import ManualClock
from swarmguard.evaluation import runner as runner_mod
from swarmguard.evaluation.datasets import build_dataset
from swarmguard.evaluation.experiment import Stage2ExperimentConfig
from swarmguard.evaluation.metrics import (
    ConfusionMatrix,
    accuracy,
    detection_latency,
    f1_score,
    false_positive_rate,
    fmt,
    precision,
    prevention_rate,
    recall,
)
from swarmguard.evaluation.results import RESULT_FILES, load_results, save_results
from swarmguard.evaluation.runner import MonitorSideEffectError, observe_scenario, run_stage2_experiment
from swarmguard.models.events import EventType
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from swarmguard.monitoring.local_monitor import LocalActionMonitor
from tests.conftest import run

SUBSET = ["distributed_approval_001", "task_decomposition_001", "legitimate_collaboration_002",
          "stale_authorization_001"]


@pytest.fixture(scope="module")
def results():
    cfg = Stage2ExperimentConfig(experiment_id="t_exp", scenario_ids=SUBSET)
    return run(run_stage2_experiment(cfg, clock=ManualClock()))


def test_25_monitors_evaluate_identical_snapshots(results):
    by_step: dict[tuple, set[str]] = {}
    for c in results.checkpoints:
        by_step.setdefault((c.scenario_id, c.step), set()).add(c.snapshot_digest)
    assert by_step and all(len(d) == 1 for d in by_step.values())
    assert {c.configuration for c in results.checkpoints} == {"local_action", "global_workflow"}


class SpyMonitor:
    def __init__(self, inner):
        self._inner, self.window, self.monitor_id, self.seen = inner, inner.window, inner.monitor_id, []

    async def evaluate(self, action, context):
        self.seen.append(json.dumps(context.model_dump(mode="json")))
        return await self._inner.evaluate(action, context)


def test_26_ground_truth_not_provided_to_monitors(monkeypatch):
    spies = {"local_action": SpyMonitor(LocalActionMonitor()), "global_workflow": SpyMonitor(GlobalWorkflowMonitor())}
    for item in build_dataset(0):
        truth = item.scenario.ground_truth()
        monkeypatch.setattr(item.scenario, "ground_truth", lambda: (_ for _ in ()).throw(AssertionError("read")))
        sim, *_ = run(observe_scenario(item, spies))  # would raise if ground truth were read while observing
        sim.close()
        for blob in (s for spy in spies.values() for s in spy.seen):
            for leak in (truth.scenario_id, truth.rationale, truth.description, "violated_invariants",
                         "expected_unsafe", "ground_truth"):
                assert leak not in blob, leak
        for spy in spies.values():
            spy.seen.clear()


def test_27_metric_calculations():
    m = ConfusionMatrix(tp=3, fp=1, tn=4, fn=2)
    assert precision(m) == 0.75 and recall(m) == 0.6 and false_positive_rate(m) == 0.2
    assert f1_score(m) == pytest.approx(2 * 0.75 * 0.6 / 1.35) and accuracy(m) == 0.7
    assert detection_latency([5, None, 2], [7, 9, 2]) == {"detected": 2, "undetected": 1, "mean_lead_steps": 1.0,
                                                          "min_lead_steps": 0, "max_lead_steps": 2}
    assert prevention_rate([True, False, False, False]) == 0.75


def test_28_confusion_matrix_matches_predictions(results):
    for cfg, m in results.metrics["detection"].items():
        rows = [p for p in results.predictions if p.configuration == cfg]
        tp = sum(p.ground_truth == "UNSAFE" and p.prediction == "UNSAFE" for p in rows)
        fp = sum(p.ground_truth == "SAFE" and p.prediction == "UNSAFE" for p in rows)
        tn = sum(p.ground_truth == "SAFE" and p.prediction != "UNSAFE" for p in rows)
        fn = sum(p.ground_truth == "UNSAFE" and p.prediction != "UNSAFE" for p in rows)
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (tp, fp, tn, fn)
        assert tp + fp + tn + fn == len(SUBSET)


def test_29_undefined_metrics_are_explicit():
    empty = ConfusionMatrix()
    assert precision(empty) is None and recall(empty) is None and f1_score(empty) is None
    assert accuracy(empty) is None and false_positive_rate(ConfusionMatrix(tp=2)) is None
    assert f1_score(ConfusionMatrix(fn=3, tn=1)) is None  # recall 0, precision undefined
    assert prevention_rate([]) is None and fmt(None) == "undefined" and fmt(0.0) == "0.000"
    assert detection_latency([None], [4])["mean_lead_steps"] is None


def test_30_31_32_export_json_csv_and_metadata(results, tmp_path):
    out = save_results(results, tmp_path)
    assert sorted(p.name for p in out.iterdir()) == sorted(RESULT_FILES)
    loaded = load_results(tmp_path, "t_exp")
    assert loaded.model_dump(mode="json") == results.model_dump(mode="json")
    with (out / "summary.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(results.predictions) == 2 * len(SUBSET)
    assert {r["configuration"] for r in rows} == {"local_action", "global_workflow"}
    meta = json.loads((out / "config.json").read_text())["metadata"]
    for key in ("executed_at", "ruleset_version", "scenario_versions", "code_revision", "random_seed",
                "scenario_count", "monitor_implementations", "positive_mapping"):
        assert key in meta, key
    assert meta["scenario_count"] == len(SUBSET) and set(meta["scenario_versions"]) == set(SUBSET)
    assert json.loads((out / "config.json").read_text())["is_research_result"] is False


def test_33_outputs_reproducible_with_identical_configuration(results):
    again = run(run_stage2_experiment(Stage2ExperimentConfig(experiment_id="t_exp", scenario_ids=SUBSET),
                                      clock=ManualClock()))
    assert again.model_dump(mode="json") == results.model_dump(mode="json")
    other = run(run_stage2_experiment(Stage2ExperimentConfig(experiment_id="t_exp", scenario_ids=SUBSET, seed=7),
                                      clock=ManualClock()))
    key = lambda r: sorted((p.scenario_id, p.configuration, p.prediction, p.first_detection_step)  # noqa: E731
                           for p in r.predictions)
    assert key(other) == key(results)  # order / workflow-ID independent
    assert other.metrics["detection"] == results.metrics["detection"]


def test_prediction_fields_come_from_execution(results):
    for p in results.predictions:
        steps = [c for c in results.checkpoints if c.scenario_id == p.scenario_id and c.configuration == p.configuration]
        assert p.checkpoints == len(steps) and p.alert_checkpoints == sum(c.alert for c in steps)
        assert p.first_detection_step == next((c.step for c in steps if c.alert), None)


def test_runner_detects_monitor_side_effects(monkeypatch):
    captured = {}
    original = SwarmSimulator.observation_builder

    def capture(self):
        captured["sim"] = self
        return original(self)

    monkeypatch.setattr(SwarmSimulator, "observation_builder", capture)

    class Mutating(SpyMonitor):
        async def evaluate(self, action, context):
            captured["sim"].monitor.record_event(EventType.SECURITY_ALERT, context.workflow_id, agent_id="x",
                                                 summary="mutation")
            return await self._inner.evaluate(action, context)

    item = build_dataset(0, ["legitimate_collaboration_001"])[0]
    with pytest.raises(MonitorSideEffectError):
        run(observe_scenario(item, {"global_workflow": Mutating(GlobalWorkflowMonitor())}))


def test_43_evaluation_runner_cli_completes(tmp_path, capsys):
    import run_evaluation

    assert run(run_evaluation.main(tmp_path, "cli_run", 0, ["approval_mismatch_001",
                                                            "legitimate_collaboration_001"])) == 0
    out = capsys.readouterr().out
    assert "Scenarios evaluated: 2" in out and "Precision:" in out and str(tmp_path) in out
    assert (tmp_path / "cli_run" / "metrics.json").is_file()


def test_runner_rejects_unknown_configurations():
    with pytest.raises(ValueError):
        run(runner_mod.run_stage2_experiment(Stage2ExperimentConfig(experiment_id="x", monitor_configurations=["oracle"])))
