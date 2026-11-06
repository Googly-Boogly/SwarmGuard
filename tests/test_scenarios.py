"""Scenario library (spec items 18-24) plus structural guarantees for every scenario."""

from __future__ import annotations

import pytest

from swarmguard.agents.simulator import RESEARCH_ID
from swarmguard.models.approvals import ApprovalStatus
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.models.events import EventType
from swarmguard.models.levels import Recommendation
from swarmguard.models.risk import PatternId
from swarmguard.models.workflows import EdgeType
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from tests.conftest import run
from tests.stage2_helpers import SCENARIOS, contexts, run_scenario


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_every_scenario_matches_its_expected_authorization_and_uses_the_gateway(sid):
    sim, r = run_scenario(sid)
    gt = SCENARIOS[sid].ground_truth()
    d = sim.monitor.get_decision_for_action(r.critical_action_id)
    assert (d.decision, d.reason_code) == (gt.expected_authorization, gt.expected_reason_code)
    actions = sim.monitor.get_workflow_actions("wf_001")
    assert len(sim.monitor.get_decisions("wf_001")) == len(actions)  # every action passed the gateway
    if gt.prohibited_outcome is not None:  # observation mode: the outcome follows the gateway decision only
        assert gt.prohibited_outcome.occurred(sim.sent_emails("wf_001")) is (d.decision is Decision.ALLOW)
    assert sid not in str([e.model_dump() for e in sim.monitor.get_workflow_history("wf_001")])
    assert gt.rationale and gt.participating_agents


def test_18_approval_mismatch_is_blocked():
    sim, r = run_scenario("approval_mismatch_001")
    d = sim.monitor.get_decision_for_action(r.critical_action_id)
    assert (d.decision, d.reason_code, d.executed) == (Decision.BLOCK, ReasonCode.APPROVAL_MISMATCH, False)
    assert sim.sent_emails("wf_001") == []


def test_19_distributed_laundering_uses_only_valid_authorizations():
    sim, r = run_scenario("distributed_approval_002")
    assert all(x.executed for x in r.responses), "every step is individually authorized"
    approvals = sim.approvals.list_for_workflow("wf_001")
    assert [a.status for a in approvals] == [ApprovalStatus.INVALIDATED, ApprovalStatus.CONSUMED]
    assert all(sim.approvals.verify_integrity(a) for a in approvals)
    reasons = {d.reason_code for d in sim.monitor.get_decisions("wf_001")}
    assert reasons == {ReasonCode.AUTHORIZED}  # no mismatch, forgery, staleness or expiry anywhere
    (sent,) = sim.sent_emails("wf_001")
    assert sent["approval_id"] == "approval_002" and sent["resource_version"] == 2


def test_20_shared_memory_manipulation_is_recorded():
    sim, r = run_scenario("shared_memory_001")
    versions = sim.workspace.versions("brief_wf_001")
    contaminated = versions[-1]
    assert contaminated.agent_id == RESEARCH_ID and "Riley Placeholder" in contaminated.content["content"]
    mods = sim.monitor.find_events(resource_id="brief_wf_001", event_type=EventType.RESOURCE_MODIFIED)
    assert [m.resource_version_after for m in mods] == [v.version for v in versions[1:]]
    reads = sim.monitor.find_events(resource_id="brief_wf_001", event_type=EventType.RESOURCE_READ)
    assert any(e.agent_id == "operations_agent" and e.resource_version_after == contaminated.version for e in reads)


def test_21_task_decomposition_creates_dependency_records():
    sim, r = run_scenario("task_decomposition_001")
    tasks = [t for t in sim.workspace.list(workflow_id="wf_001") if t.resource_type.value == "task"]
    assert len(tasks) == 3
    final = next(t for t in tasks if t.content["assignee"] == "operations_agent")
    assert len(final.content["depends_on_task_ids"]) == 2
    assign_actions = [a for a in sim.monitor.get_workflow_actions("wf_001") if a.tool == "assign_task"]
    assert set(assign_actions[2].parent_action_ids) == {assign_actions[0].action_id, assign_actions[1].action_id}
    graph = sim.graph.build_workflow_graph("wf_001")
    assert len(graph.edges_of(EdgeType.ASSIGNED_TO)) == 3


@pytest.mark.parametrize("sid", [s for s, sc in SCENARIOS.items() if not sc.ground_truth().expected_unsafe])
def test_22_legitimate_workflows_remain_authorized(sid):
    sim, r = run_scenario(sid)
    d = sim.monitor.get_decision_for_action(r.critical_action_id)
    assert d.decision is Decision.ALLOW and d.executed and sim.sent_emails("wf_001")


def test_23_legitimate_shared_changes_not_automatically_harmful():
    sim, r = run_scenario("legitimate_shared_update_001")
    _, glob = contexts(sim, "wf_001", r.critical_action_id)
    a = run(GlobalWorkflowMonitor().evaluate(glob.action, glob))
    assert PatternId.CROSS_AGENT_RESOURCE_CHANGE in a.detected_patterns
    assert a.recommendation is Recommendation.ALLOW


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_24_scenario_execution_is_reproducible(sid):
    def snap():
        sim, r = run_scenario(sid)
        b = sim.observation_builder()
        out = ([e.model_dump(mode="json") for e in sim.monitor.get_workflow_history("wf_001")],
               [d.model_dump(mode="json") for d in sim.monitor.get_decisions("wf_001")],
               [b.snapshot("wf_001", seq).digest() for _, seq in b.checkpoints("wf_001")], sim.sent_emails())
        sim.close()
        return out
    assert snap() == snap()
