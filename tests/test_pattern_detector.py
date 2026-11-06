"""Deterministic pattern detectors: explicit evidence, no harm inferred from mere collaboration."""

from __future__ import annotations

from swarmguard.models.levels import RiskLevel
from swarmguard.models.risk import EvidenceBasis, PatternId
from swarmguard.security.pattern_detector import detect_all
from tests.stage2_helpers import action_of_tool, contexts, run_scenario


def _findings(ctx, pattern):
    return [f for f in detect_all(ctx).findings if f.pattern_id is pattern]


def test_approval_scope_drift_on_mismatch_is_explicit_and_critical():
    sim, r = run_scenario("approval_mismatch_001")
    local, glob = contexts(sim, "wf_001", r.critical_action_id)
    for ctx in (local, glob):
        drift = _findings(ctx, PatternId.APPROVAL_SCOPE_DRIFT)
        assert any(f.severity is RiskLevel.CRITICAL and f.basis is EvidenceBasis.EXPLICIT and f.is_violation
                   for f in drift)
        assert "resource_version" in next(f for f in drift if f.severity is RiskLevel.CRITICAL).explanation
    # only the global window can see the modification that happened after the approval
    assert any(f.severity is RiskLevel.MEDIUM for f in _findings(glob, PatternId.APPROVAL_SCOPE_DRIFT))
    assert not any(f.severity is RiskLevel.MEDIUM for f in _findings(local, PatternId.APPROVAL_SCOPE_DRIFT))


def test_cross_agent_resource_change_is_never_a_violation():
    sim, r = run_scenario("legitimate_shared_update_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    read_brief = action_of_tool(sim, "wf_001", "read_document", nth=-1)
    _, glob_read = contexts(sim, "wf_001", read_brief)
    found = _findings(glob, PatternId.CROSS_AGENT_RESOURCE_CHANGE) + _findings(glob_read, PatternId.CROSS_AGENT_RESOURCE_CHANGE)
    assert found, "multi-agent shared modification must be reported"
    assert all(not f.is_violation and not f.actionable and f.severity.rank <= RiskLevel.MEDIUM.rank for f in found)


def test_protected_data_flow_distinguishes_authorized_recipients():
    sim, r = run_scenario("legitimate_collaboration_001")
    _, ok = contexts(sim, "wf_001", r.critical_action_id)
    (flow,) = _findings(ok, PatternId.PROTECTED_DATA_FLOW)
    assert flow.severity is RiskLevel.LOW and flow.basis is EvidenceBasis.HEURISTIC

    sim2, r2 = run_scenario("distributed_approval_001")
    _, bad = contexts(sim2, "wf_001", r2.critical_action_id)
    (flow2,) = _findings(bad, PatternId.PROTECTED_DATA_FLOW)
    assert flow2.severity is RiskLevel.HIGH
    (trace,) = detect_all(bad).flows
    relations = [h.relation for h in trace.hops]
    assert relations[0] == "SOURCE_READ" and relations[-1] == "OUTBOUND_ACTION" and "CONTAINS_VALUE" in relations
    assert trace.hops[0].basis is EvidenceBasis.EXPLICIT and trace.recipient_authorized is False
    assert set(trace.protected_fields) == {"name", "request_id"}


def test_cumulative_disclosure_counts_across_operations():
    sim, r = run_scenario("task_decomposition_001")
    first_send = action_of_tool(sim, "wf_001", "send_email", nth=0)
    _, at_first = contexts(sim, "wf_001", first_send)
    _, at_second = contexts(sim, "wf_001", r.critical_action_id)
    assert [f.severity for f in _findings(at_first, PatternId.CUMULATIVE_DISCLOSURE)] == [RiskLevel.LOW]
    (cum,) = _findings(at_second, PatternId.CUMULATIVE_DISCLOSURE)
    assert cum.severity is RiskLevel.HIGH and "postcode" in cum.explanation and "birth_year" in cum.explanation
    assert _findings(at_second, PatternId.SUSPICIOUS_TASK_DECOMPOSITION)


def test_paraphrased_values_are_not_detected_documented_limitation():
    sim, r = run_scenario("transformed_disclosure_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    assert _findings(glob, PatternId.PROTECTED_DATA_FLOW) == []


def test_detectors_do_not_depend_on_identifiers():
    """Renaming the workflow to a label-like ID changes nothing but the IDs themselves."""
    a, ra = run_scenario("legitimate_collaboration_001", workflow_id="wf_001")
    b, rb = run_scenario("legitimate_collaboration_001", workflow_id="attack_unsafe_laundering")
    fa = detect_all(contexts(a, "wf_001", ra.critical_action_id)[1]).findings
    fb = detect_all(contexts(b, "attack_unsafe_laundering", rb.critical_action_id)[1]).findings
    assert [(f.pattern_id, f.severity, f.actionable) for f in fa] == [(f.pattern_id, f.severity, f.actionable) for f in fb]
