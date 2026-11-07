"""RQ1 harness and held-out dataset tests.

Monitors are evaluated here only on *development* scenarios. No test evaluates a monitor on the held-out
library, so the test suite cannot reveal held-out monitor results before the frozen primary run.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from swarmguard.evaluation import rq1
from swarmguard.evaluation.datasets import build_dataset
from swarmguard.evaluation.experiment import Stage2ExperimentConfig
from swarmguard.evaluation.runner import run_stage2_experiment
from swarmguard.evaluation.scenarios.rq1_heldout import HELDOUT_CUSTOMERS, rq1_heldout_scenarios


def test_heldout_composition_matches_protocol_table():
    scenarios = rq1_heldout_scenarios()
    assert len({s.scenario_id for s in scenarios}) == 24
    counts = Counter((s.category, s.ground_truth().label.value) for s in scenarios)
    assert counts == {
        ("distributed_approval_laundering", "UNSAFE"): 6, ("distributed_approval_laundering", "SAFE"): 6,
        ("cross_agent_information_flow", "UNSAFE"): 3, ("cross_agent_information_flow", "SAFE"): 3,
        ("shared_resource_coordination", "UNSAFE"): 3, ("shared_resource_coordination", "SAFE"): 3,
    }


def test_heldout_unsafe_scenarios_have_independent_oracles_and_allowed_authorization():
    for s in rq1_heldout_scenarios():
        gt = s.ground_truth()
        assert gt.expected_authorization.value == "ALLOW"  # no invalid-approval cases in the RQ1 set
        assert (gt.prohibited_outcome is not None) == gt.expected_unsafe
        assert gt.rationale and gt.description


def test_heldout_records_use_reserved_domains_only():
    for record in HELDOUT_CUSTOMERS.values():
        for address in [record["email"], *record["authorized_recipients"]]:
            assert address.rsplit(".", 1)[-1] in {"test", "com", "org", "net"}
            assert any(address.endswith(d) for d in ("example.test", "example.com", "example.org", "example.net"))


def test_manifest_hash_is_deterministic_and_content_addressed():
    a, b = rq1.dataset_manifest("held_out"), rq1.dataset_manifest("held_out")
    assert a["content_hash"] == b["content_hash"] and a["n"] == 24
    assert rq1.dataset_manifest("development")["content_hash"] != a["content_hash"]


def test_build_dataset_default_unchanged_and_custom_ids_neutral():
    default = build_dataset(0)
    assert len(default) == 14
    custom = build_dataset(0, scenarios=rq1_heldout_scenarios())
    assert [i.workflow_id for i in custom] == [f"wf_{n:03d}" for n in range(1, 25)]
    assert all(i.scenario.scenario_id not in i.workflow_id for i in custom)


def test_heldout_dataset_validation_is_monitor_free_and_valid():
    report = asyncio.run(rq1.validate_dataset("held_out"))
    assert report["monitors_evaluated"] == 0
    assert report["all_valid"], [r for r in report["scenarios"] if not r["valid"]]


def test_exact_mcnemar_and_wilson():
    assert rq1.exact_mcnemar(0, 0) is None
    assert rq1.exact_mcnemar(1, 0) == 1.0
    assert rq1.exact_mcnemar(6, 0) == pytest.approx(2 / 64)
    assert rq1.exact_mcnemar(3, 3) == 1.0
    assert rq1.wilson(0, 0) is None
    ci = rq1.wilson(5, 10)
    assert ci["low"] < 0.5 < ci["high"]


def test_pilot_harness_checks_pass_on_development_scenarios():
    async def go():
        cfg = Stage2ExperimentConfig(experiment_id="t_rq1", seed=0, scenario_ids=list(rq1.PILOT_SCENARIOS),
                                     enforcement_configurations=["deterministic_only"])
        scenarios = rq1.scenarios_for("development")
        results = await run_stage2_experiment(cfg, revision="test", scenarios=scenarios)
        return results, await rq1.verify_observations(results, scenarios, timing_repeats=1)

    results, verification = asyncio.run(go())
    assert verification["checks_passed"], verification["failures"]
    manifest = rq1.dataset_manifest("development")
    manifest = {**manifest, "scenarios": [s for s in manifest["scenarios"] if s["scenario_id"] in rq1.PILOT_SCENARIOS]}
    analysis = rq1.analyze(results, manifest)
    assert analysis["n_workflows"] == 2 and not analysis["missing_or_failed"]
    assert analysis["estimated_cost_usd"] == 0.0


def test_export_refuses_to_overwrite(tmp_path):
    (tmp_path / "exp").mkdir()

    class R:
        experiment_id = "exp"

    with pytest.raises(FileExistsError):
        rq1.export_run(R(), {}, {}, {}, tmp_path)  # type: ignore[arg-type]
