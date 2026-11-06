"""Stage 3 comparative evaluation (spec items 31-40)."""

from __future__ import annotations

import csv
import json

import pytest

from swarmguard.clock import ManualClock
from swarmguard.ethics.aggregator import AggregationMethod
from swarmguard.ethics.prompts import prompt_hash
from swarmguard.ethics.synthetic import rules_responder
from swarmguard.evaluation.ethics_comparison import (
    STAGE3_FILES,
    EthicsExperimentConfig,
    ExperimentExistsError,
    load_ethics_results,
    run_ethics_experiment,
    save_ethics_results,
)
from swarmguard.llm.configuration import LLMSettings
from swarmguard.llm.mock_provider import MockBehavior, MockLLMProvider, MockScript, MockStep
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider
from swarmguard.models.assessments import EthicalFramework
from swarmguard_providers.factory import build_provider
from tests.conftest import run
from tests.stage3_helpers import judgment

SUBSET = ["distributed_approval_001", "legitimate_collaboration_001", "approval_mismatch_001"]


def _run(provider=None, guard=None, **cfg):
    settings = LLMSettings()
    if provider is None:
        provider, guard = build_provider(settings, mock_responder=rules_responder)
    config = EthicsExperimentConfig(experiment_id=cfg.pop("experiment_id", "t3"), scenario_ids=cfg.pop("ids", SUBSET),
                                    **cfg)
    return run(run_ethics_experiment(config, provider, settings, guard=guard, clock=ManualClock())), provider


@pytest.fixture(scope="module")
def mock_results():
    return _run()


def _strip_timing(obj):
    if isinstance(obj, dict):
        return {k: _strip_timing(v) for k, v in obj.items()
                if k not in ("latency_ms", "elapsed_ms_total", "mean_council_latency_ms", "elapsed_ms")}
    if isinstance(obj, list):
        return [_strip_timing(x) for x in obj]
    return obj


def test_31_councils_receive_equivalent_evidence(mock_results):
    results, provider = mock_results
    by_step: dict = {}
    for c in results.councils:
        by_step.setdefault((c.scenario_id, c.step), set()).add(c.snapshot_digest)
    assert all(len(d) == 1 for d in by_step.values())
    calls = provider.inner._inner.calls if hasattr(provider, "inner") else provider.calls
    by_action: dict = {}
    for call in calls:
        wf = json.loads(call["user_content"])["untrusted_workflow_data"]["observation"]["workflow_id"]
        by_action.setdefault((wf, call["tags"]["action_id"]), set()).add(call["user_content"])
    assert by_action and all(len(v) == 1 for v in by_action.values())  # A, B and C saw byte-identical evidence


def test_32_same_aggregation_method_for_both_councils(mock_results):
    results, _ = mock_results
    methods = {c.aggregation_method for c in results.councils if c.configuration in ("B_general_x4", "C_specialized_x4")}
    assert methods == {"MAJORITY_WITH_ESCALATION"}
    assert results.metadata["aggregation"]["method"] == results.config["aggregation_method"]


def test_33_ground_truth_hidden_from_monitors(monkeypatch):
    from swarmguard.evaluation.scenarios.base import Scenario
    seen: list[str] = []

    def spy(system_prompt, user_content, tags):
        seen.append(system_prompt + user_content)
        return rules_responder(system_prompt, user_content, tags)

    settings = LLMSettings()
    provider, guard = build_provider(settings, mock_responder=spy)
    results, _ = _run(provider, guard)
    for row in results.ground_truth:
        for blob in seen:
            for leak in (row["scenario_id"], row["rationale"], row["description"], "violated_invariants"):
                assert leak not in blob


def test_34_37_38_results_persisted_with_prompt_and_model_versions(mock_results, tmp_path):
    results, _ = mock_results
    out = save_ethics_results(results, tmp_path)
    assert sorted(p.name for p in out.iterdir()) == sorted(STAGE3_FILES)
    loaded = load_ethics_results(tmp_path, "t3")
    meta = loaded["experiment_config"]["metadata"]
    assert meta["prompts"]["prompt_version"] == "ethics_v1"
    assert meta["prompts"]["prompts"]["deontology"]["sha256"] == prompt_hash(EthicalFramework.DEONTOLOGY)
    assert meta["provider"]["provider"] == "mock" and meta["model"] == "mock-deterministic-v1"
    assert meta["provider_is_synthetic"] is True and "api_key" not in json.dumps(meta["provider"]).replace(
        "api_key_configured", "")
    assert all(a["prompt_hash"] and a["model"] for a in loaded["monitor_assessments"] if a["success"])
    with (out / "summary.csv").open() as fh:
        assert len(list(csv.DictReader(fh))) == len(results.predictions)
    with pytest.raises(ExperimentExistsError):
        save_ethics_results(results, tmp_path)  # never overwrites a prior run silently


def test_35_metrics_match_predictions(mock_results):
    results, _ = mock_results
    for cfg, m in results.metrics["detection"].items():
        rows = [p for p in results.predictions if p.configuration == cfg]
        assert m["tp"] == sum(p.ground_truth == "UNSAFE" and p.prediction == "UNSAFE" for p in rows)
        assert m["fp"] == sum(p.ground_truth == "SAFE" and p.prediction == "UNSAFE" for p in rows)
        assert m["tp"] + m["fp"] + m["tn"] + m["fn"] == len(SUBSET)
    op = results.metrics["operational"]["C_specialized_x4"]
    assert op["monitor_calls"] == 4 * op["council_evaluations_attempted"] and op["evidence_reference_validity"] == 1.0


def test_36_failed_evaluations_remain_visible():
    flaky = MockLLMProvider(MockScript(default=MockStep(response=judgment("ALLOW", "LOW")),
                                       by_tag={"care_ethics": MockStep(MockBehavior.AUTHENTICATION),
                                               "llm_general_purpose_2": MockStep(response=judgment(
                                                   evidence=["event_999"]))}))
    guard = GuardedProvider(flaky, BudgetPolicy())
    results, _ = _run(guard, guard, ids=["legitimate_collaboration_001"])
    op = results.metrics["operational"]
    assert op["C_specialized_x4"]["failures_by_kind"] == {"AUTHENTICATION": op["C_specialized_x4"]["failed_assessments"]}
    assert op["B_general_x4"]["invalid_evidence_assessments"] > 0 and op["B_general_x4"]["evidence_reference_validity"] < 1
    failed = [a for a in results.assessments if not a.success]
    assert failed and all(a.error_kind for a in failed)
    # incomplete councils escalate -> visible as positives, never silently ALLOW
    c_rows = [c for c in results.councils if c.configuration == "C_specialized_x4"]
    assert all(c.recommendation != "ALLOW" and c.failed_monitors == ["llm_care_ethics"] for c in c_rows)


def test_39_token_usage_recorded_when_available():
    provider = MockLLMProvider(MockScript(default=MockStep(response=judgment("ALLOW", "LOW"), usage=(100, 20))))
    guard = GuardedProvider(provider, BudgetPolicy(price_input_per_mtok=2.0, price_output_per_mtok=8.0))
    results, _ = _run(guard, guard, ids=["legitimate_collaboration_001"], configurations=["C_specialized_x4"])
    op = results.metrics["operational"]["C_specialized_x4"]
    n = op["monitor_calls"]
    assert op["input_tokens"] == 100 * n and op["output_tokens"] == 20 * n
    assert op["estimated_cost_usd"] == pytest.approx(n * (100 * 2 + 20 * 8) / 1e6)
    unpriced, _ = _run(ids=["legitimate_collaboration_001"], configurations=["C_specialized_x4"])
    assert unpriced.metrics["operational"]["C_specialized_x4"]["input_tokens"] is None  # unavailable, not 0


def test_40_mock_evaluations_reproducible(mock_results):
    again, _ = _run()
    assert _strip_timing(again.model_dump(mode="json")) == _strip_timing(mock_results[0].model_dump(mode="json"))


def test_budget_exhaustion_terminates_early_and_is_recorded():
    provider = MockLLMProvider(MockScript(default=MockStep(response=judgment("ALLOW", "LOW"))))
    guard = GuardedProvider(provider, BudgetPolicy(max_requests=6))
    results, _ = _run(guard, guard, ids=["legitimate_collaboration_001", "distributed_approval_001"])
    assert results.metrics["terminated_early"] and results.metadata["terminated_early"]
    assert any(not c.attempted for c in results.councils) and len(provider.calls) == 6
    assert results.metrics["provider_ledger"]["refused"] >= 1


def test_aggregation_is_fixed_in_config_and_recorded():
    results, _ = _run(ids=["legitimate_collaboration_001"], aggregation_method=AggregationMethod.CONSERVATIVE,
                      configurations=["B_general_x4", "C_specialized_x4"])
    assert {c.aggregation_method for c in results.councils} == {"CONSERVATIVE"}
    assert results.config["aggregation_method"] == "CONSERVATIVE"


def test_mock_run_is_labelled_synthetic(mock_results):
    results, _ = mock_results
    assert results.is_research_result is False and results.note.startswith("SYNTHETIC MOCK RUN")
    assert all(a.is_synthetic for a in results.assessments)
