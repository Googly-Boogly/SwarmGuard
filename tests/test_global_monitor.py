"""Global Workflow Monitor (spec items 12-17)."""

from __future__ import annotations

import pytest

from swarmguard.models.approvals import ApprovalStatus
from swarmguard.models.events import EventType
from swarmguard.models.levels import Recommendation, Uncertainty
from swarmguard.models.risk import PatternId
from swarmguard.monitoring.base import MonitorInputError
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from tests.conftest import run
from tests.stage2_helpers import action_of_tool, contexts, run_scenario


def _eval(ctx):
    return run(GlobalWorkflowMonitor().evaluate(ctx.action, ctx))


def test_12_receives_relevant_workflow_history():
    sim, r = run_scenario("distributed_approval_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    types = {e.event_type for e in glob.events}
    assert {EventType.TASK_ASSIGNED, EventType.RESOURCE_READ, EventType.RESOURCE_MODIFIED,
            EventType.APPROVAL_GRANTED} <= types
    assert any(x.resource_id == "cust_201" for x in glob.resources) and glob.workflow_purpose == "service_updates"
    assert glob.prior_actions and all(a.action_id != r.critical_action_id for a in glob.prior_actions)


def test_13_identifies_explicit_invariant_violations():
    sim, r = run_scenario("distributed_approval_001")
    a = _eval(contexts(sim, "wf_001", r.critical_action_id)[1])
    assert a.recommendation is Recommendation.ESCALATE
    assert PatternId.WORKFLOW_INVARIANT_VIOLATION in a.detected_patterns
    assert any(f.invariant_id == "customer_data_disclosure" for f in a.findings)


def test_14_traces_resource_dependencies():
    sim, r = run_scenario("shared_memory_001")
    a = _eval(contexts(sim, "wf_001", r.critical_action_id)[1])
    flows = {f.source_resource_id: f for f in a.data_flows}
    assert "cust_201" in flows and flows["cust_201"].recipient_authorized is False
    assert flows["cust_202"].recipient_authorized is True
    hops = [(h.resource_id, h.relation) for h in flows["cust_201"].hops]
    assert ("cust_201", "SOURCE_READ") in hops and any(rid == "brief_wf_001" for rid, _ in hops)


def test_15_handles_incomplete_history():
    sim, r = run_scenario("distributed_approval_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    partial = glob.model_copy(update={"resources": [x for x in glob.resources if x.resource_id != "cust_201"]})
    a = _eval(partial)
    assert a.recommendation is Recommendation.ALLOW and a.uncertainty is Uncertainty.HIGH
    assert any("provenance" in m for m in a.missing_information)


def test_16_references_valid_evidence():
    sim, r = run_scenario("task_decomposition_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    a = _eval(glob)
    assert a.evidence_event_ids and set(a.evidence_event_ids) <= glob.event_ids()
    assert all(sim.monitor.get_event(e).seq <= a.checkpoint_seq for e in a.evidence_event_ids)


def test_17_cannot_access_future_events():
    sim, r = run_scenario("distributed_approval_002")
    wf = "wf_001"
    req2 = action_of_tool(sim, wf, "request_approval", nth=1)
    builder = sim.observation_builder()
    snap = builder.snapshot_for_action(req2)
    assert max(e.seq for e in snap.events) == snap.cutoff_seq == builder.proposal_seq(req2)
    assert all((x.event_seq or 0) <= snap.cutoff_seq for x in snap.resources)
    assert not any(x.resource_id.startswith("email_") and x.version > 2 for x in snap.resources)
    # approval states are replayed as of the checkpoint, not read from the (later) current rows
    views = {v.approval_id: v.status for v in snap.approvals}
    assert views == {"approval_001": ApprovalStatus.INVALIDATED}  # approval_002 is not requested yet
    assert sim.approvals.get("approval_001").status is ApprovalStatus.INVALIDATED
    assert sim.approvals.get("approval_002").status is ApprovalStatus.CONSUMED
    send = r.critical_action_id
    _, at_send = contexts(sim, wf, send)
    assert {v.approval_id: v.status for v in at_send.approvals}["approval_002"] is ApprovalStatus.APPROVED
    tampered = at_send.model_copy(update={"events": [*at_send.events, *[
        e for e in sim.monitor.get_workflow_history(wf) if e.seq > at_send.checkpoint_seq]]})
    with pytest.raises(MonitorInputError):
        run(GlobalWorkflowMonitor().evaluate(tampered.action, tampered))


def test_17b_unreferenced_later_versions_are_not_visible():
    """Regression: a record version created outside the workflow log must not leak into the window."""
    sim, r = run_scenario("stale_authorization_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    assert [x.version for x in glob.resources if x.resource_id == "cust_203"] == [1]
    assert sim.workspace.get("cust_203").version == 2
