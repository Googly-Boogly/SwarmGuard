"""Ethics council orchestration (spec items 21-25, 30)."""

from __future__ import annotations


from swarmguard.ethics.aggregator import AggregationMethod, Aggregator
from swarmguard.ethics.general_council import build_general_council, build_specialized_council
from swarmguard.llm.mock_provider import MockBehavior, MockLLMProvider, MockScript, MockStep
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider
from swarmguard.models.levels import Recommendation
from tests.conftest import run
from tests.stage3_helpers import judgment, scenario_context


def _scripted(**by_tag: MockStep) -> MockLLMProvider:
    return MockLLMProvider(MockScript(default=MockStep(response=judgment("ALLOW", "LOW")), by_tag=by_tag))


def test_21_22_monitors_execute_independently_and_assessments_are_preserved():
    sim, action, ctx = scenario_context()
    provider = _scripted(virtue_ethics=MockStep(response=judgment("BLOCK", "CRITICAL", concerns=["dishonest"])),
                         care_ethics=MockStep(response=judgment("ESCALATE", "MEDIUM", concerns=["trust"])))
    report = run(build_specialized_council(provider).deliberate(action, ctx))
    assert len(provider.calls) == 4 and len(report.assessments) == 4
    for call in provider.calls:  # no monitor ever sees another monitor's output
        assert "dishonest" not in call["user_content"] and "llm_" not in call["user_content"]
    by = {a.framework.value: a for a in report.assessments}
    assert by["virtue_ethics"].recommendation is Recommendation.BLOCK and by["virtue_ethics"].concerns == ["dishonest"]
    assert by["care_ethics"].concerns == ["trust"] and by["deontology"].recommendation is Recommendation.ALLOW


def test_23_disagreement_preserved():
    sim, action, ctx = scenario_context()
    provider = _scripted(virtue_ethics=MockStep(response=judgment("ESCALATE")),
                         deontology=MockStep(response=judgment("BLOCK")))
    report = run(build_specialized_council(provider).deliberate(action, ctx))
    assert report.disagreement_detected and report.disagreement["distinct_recommendations"] == 3
    assert {p.recommendation for p in report.positions} == {Recommendation.ALLOW, Recommendation.ESCALATE,
                                                             Recommendation.BLOCK}


def test_24_25_failed_monitors_identified_and_partial_failure_handled():
    sim, action, ctx = scenario_context()
    provider = MockLLMProvider(MockScript(default=MockStep(response=judgment("ALLOW", "LOW")), by_tag={
        "care_ethics": MockStep(MockBehavior.TIMEOUT), "deontology": MockStep(MockBehavior.INVALID_JSON),
        "virtue_ethics": MockStep(MockBehavior.RAISE)}))
    report = run(build_specialized_council(GuardedProvider(provider, BudgetPolicy())).deliberate(action, ctx))
    assert set(report.failed_monitors) == {"llm_care_ethics", "llm_deontology", "llm_virtue_ethics"}
    kinds = {f.monitor_id: f.error_kind for f in report.failures}
    assert kinds == {"llm_care_ethics": "TIMEOUT", "llm_deontology": "INVALID_RESPONSE",
                     "llm_virtue_ethics": "PROVIDER_ERROR"}
    assert len(report.assessments) == 1 and report.council_recommendation is Recommendation.ESCALATE  # 30


def test_hanging_monitor_is_cut_off_by_council_timeout():
    sim, action, ctx = scenario_context()
    provider = MockLLMProvider(MockScript(default=MockStep(response=judgment("ALLOW", "LOW")),
                                          by_tag={"llm_care_ethics": MockStep(MockBehavior.HANG)}), hang_seconds=5)
    report = run(build_specialized_council(provider, timeout_seconds=0.1).deliberate(action, ctx))
    (f,) = report.failures
    assert f.monitor_id == "llm_care_ethics" and f.error_kind == "TIMEOUT" and f.elapsed_ms < 2000
    assert report.council_recommendation is Recommendation.ESCALATE


def test_30_unanimous_allow_with_one_missing_never_allows():
    sim, action, ctx = scenario_context()
    for method in AggregationMethod:
        provider = _scripted(care_ethics=MockStep(MockBehavior.AUTHENTICATION))
        report = run(build_specialized_council(provider, aggregator=Aggregator(method)).deliberate(action, ctx))
        assert report.council_recommendation is not Recommendation.ALLOW, method


def test_general_council_has_four_independent_identically_prompted_monitors():
    sim, action, ctx = scenario_context()
    provider = _scripted()
    council = build_general_council(provider)
    report = run(council.deliberate(action, ctx))
    assert council.council_type == "GENERAL_PURPOSE_X4" and len(report.assessments) == 4
    assert len({c["system_prompt"] for c in provider.calls}) == 1 and len({c["user_content"] for c in provider.calls}) == 1
    assert report.missing_frameworks == [] and report.disagreement["pairwise_recommendation_agreement"] == 1.0


def test_council_concurrency_bounded():
    sim, action, ctx = scenario_context()
    slow = MockLLMProvider(MockScript(default=MockStep(MockBehavior.HANG, response=judgment())), hang_seconds=0.02)
    g = GuardedProvider(slow, BudgetPolicy(), max_concurrency=4)
    report = run(build_specialized_council(g, max_concurrency=2).deliberate(action, ctx))
    assert len(report.assessments) == 4 and g.max_in_flight_observed <= 2
