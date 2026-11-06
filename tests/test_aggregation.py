"""Council aggregation (spec items 26-30)."""

from __future__ import annotations

import itertools

import pytest

from swarmguard.ethics.aggregator import AggregationMethod as M
from swarmguard.ethics.aggregator import Aggregator, IncompletePolicy
from swarmguard.models.levels import Recommendation as R

A, E, B = R.ALLOW, R.ESCALATE, R.BLOCK


def agg(method, recs, incomplete=False, **kw):
    return Aggregator(method, **kw).aggregate(recs, incomplete=incomplete).recommendation


def test_26_deterministic_for_given_inputs():
    for method in M:
        for recs in itertools.product([A, E, B], repeat=4):
            assert agg(method, list(recs)) is agg(method, list(recs))
            assert agg(method, list(recs)) is agg(method, list(reversed(recs)))  # order-independent


def test_27_majority_with_restrictive_tie_break():
    assert agg(M.MAJORITY, [A, A, A, E]) is A
    assert agg(M.MAJORITY, [A, A, E, E]) is E  # tie -> most restrictive
    assert agg(M.MAJORITY, [A, E, B, B]) is B and agg(M.MAJORITY, [A, A, B, E]) is A


def test_28_conservative():
    assert agg(M.CONSERVATIVE, [A, A, A, E]) is E and agg(M.CONSERVATIVE, [A, B, A, A]) is B
    assert agg(M.CONSERVATIVE, [A, A, A, A]) is A


def test_29_disagreement_escalation():
    assert agg(M.MAJORITY_WITH_ESCALATION, [A, A, A, E]) is A       # 0.25 disagreement, not > threshold
    assert agg(M.MAJORITY_WITH_ESCALATION, [A, A, E, B]) is E       # 0.5 > 0.25 -> at least ESCALATE
    assert agg(M.MAJORITY_WITH_ESCALATION, [B, B, B, A]) is B
    assert agg(M.MAJORITY_WITH_ESCALATION, [A, A, B, B]) is B
    assert agg(M.MAJORITY_WITH_ESCALATION, [A, A, A, E], disagreement_threshold=0.2) is E


def test_unanimity_matches_stage1_rule():
    assert agg(M.UNANIMITY, [A] * 4) is A and agg(M.UNANIMITY, [B] * 4) is B
    assert agg(M.UNANIMITY, [A, A, A, B]) is E


@pytest.mark.parametrize("method", list(M))
def test_30_missing_assessments_never_silently_allow(method):
    assert agg(method, [A, A, A], incomplete=True) is not A
    assert agg(method, [], incomplete=True) is E
    assert agg(method, [A, A, A], incomplete=True,
               incomplete_policy=IncompletePolicy.AGGREGATE_AVAILABLE, quorum=4) is not A
    assert agg(M.CONSERVATIVE, [B, A], incomplete=True) is B  # never weakened below what was voted


def test_aggregate_available_policy_is_explicit():
    a = Aggregator(M.MAJORITY, IncompletePolicy.AGGREGATE_AVAILABLE, quorum=3)
    out = a.aggregate([A, A, A], incomplete=True)
    assert out.recommendation is A and out.incomplete and "quorum" in out.rationale
    assert a.describe()["incomplete_policy"] == "AGGREGATE_AVAILABLE"
