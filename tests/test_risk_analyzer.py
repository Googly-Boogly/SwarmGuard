"""Risk analyzer: invariant evaluation from definitions, fixed aggregation rule, evidence integrity."""

from __future__ import annotations

import pytest

from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.risk import EvidenceBasis, InvariantKind, PatternFinding, PatternId
from swarmguard.security import risk_analyzer
from swarmguard.security.risk_analyzer import EvidenceIntegrityError, RiskAnalyzer, aggregate
from tests.stage2_helpers import contexts, run_scenario


def _f(sev, actionable, pattern=PatternId.WORKFLOW_INVARIANT_VIOLATION, basis=EvidenceBasis.EXPLICIT):
    return PatternFinding(pattern_id=pattern, severity=sev, basis=basis, is_violation=actionable,
                          actionable=actionable, explanation="x", detected_at_seq=1)


def test_aggregation_rule():
    assert aggregate([], [], False) == (RiskLevel.LOW, Recommendation.ALLOW, Uncertainty.LOW)
    assert aggregate([_f(RiskLevel.CRITICAL, True)], [], True)[1] is Recommendation.BLOCK
    assert aggregate([_f(RiskLevel.HIGH, True)], [], True)[1] is Recommendation.ESCALATE
    # a non-actionable HIGH observation raises the risk level but not the recommendation
    risk, rec, _ = aggregate([_f(RiskLevel.HIGH, False, PatternId.PROTECTED_DATA_FLOW)], [], True)
    assert (risk, rec) == (RiskLevel.HIGH, Recommendation.ALLOW)
    assert aggregate([], ["missing"], True)[2] is Uncertainty.HIGH
    assert aggregate([], ["missing"], False)[2] is Uncertainty.MEDIUM
    assert aggregate([_f(RiskLevel.HIGH, True, basis=EvidenceBasis.HEURISTIC)], [], True)[2] is Uncertainty.MEDIUM


def test_violations_come_from_invariant_definitions_not_names():
    sim, r = run_scenario("distributed_approval_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    a = RiskAnalyzer().assess(glob, monitor_id="t")
    assert a.recommendation is Recommendation.ESCALATE
    assert {f.invariant_id for f in a.findings if f.is_violation} == {"customer_data_disclosure"}
    # Same evidence without the recipient-authorization invariant: no violation is reported.
    reduced = glob.model_copy(update={"invariants": [i for i in glob.invariants
                                                     if i.kind is not InvariantKind.RECIPIENT_AUTHORIZATION]})
    b = RiskAnalyzer().assess(reduced, monitor_id="t")
    assert b.recommendation is Recommendation.ALLOW and not any(f.is_violation for f in b.findings)


def test_purpose_invariant_uses_declared_purpose():
    sim, r = run_scenario("purpose_violation_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    assert {f.invariant_id for f in RiskAnalyzer().assess(glob, monitor_id="t").findings if f.is_violation} == {
        "data_purpose_limitation"}
    unknown = RiskAnalyzer().assess(glob.model_copy(update={"workflow_purpose": None}), monitor_id="t")
    assert unknown.recommendation is Recommendation.ALLOW
    assert any("purpose is not observable" in m for m in unknown.missing_information)


def test_evidence_outside_context_is_rejected(monkeypatch):
    sim, r = run_scenario("legitimate_collaboration_001")
    local, _ = contexts(sim, "wf_001", r.critical_action_id)
    real = risk_analyzer.detect_all

    def leaky(ctx):
        result = real(ctx)
        result.findings.append(_f(RiskLevel.LOW, False).model_copy(update={"evidence_event_ids": ["event_999"]}))
        return result

    monkeypatch.setattr(risk_analyzer, "detect_all", leaky)
    with pytest.raises(EvidenceIntegrityError):
        RiskAnalyzer().assess(local, monitor_id="t")
