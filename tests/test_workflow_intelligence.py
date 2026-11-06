"""Stage 2 workflow intelligence (spec items 1-6, 44)."""

from __future__ import annotations

from swarmguard.agents.simulator import COORDINATOR_ID, OPERATIONS_ID, RESEARCH_ID
from swarmguard.models.events import EventType
from swarmguard.security.workflow_intelligence import WorkflowIntelligenceEngine, analyze_structure
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.clock import SteppingClock
from swarmguard.storage.database import Database
from tests.conftest import run
from tests.stage2_helpers import run_scenario


def _analysis(sim, wf="wf_001"):
    return run(WorkflowIntelligenceEngine(sim.monitor, builder=sim.observation_builder()).analyze_workflow(wf))


def test_1_reconstructs_multi_agent_workflow():
    sim, _ = run_scenario("distributed_approval_001")
    a = _analysis(sim)
    assert a.participating_agents == sorted([COORDINATOR_ID, RESEARCH_ID, OPERATIONS_ID])
    assert "human_operator" in a.principals
    assert {"cust_201", "brief_wf_001"} <= set(a.resources_accessed)
    assert a.checkpoint_assessments, "outbound checkpoints must be assessed"


def test_2_explicit_dependencies_preserved():
    sim, _ = run_scenario("distributed_approval_001")
    a = _analysis(sim)
    actions = {x.action_id: x for x in sim.monitor.get_workflow_actions("wf_001")}
    assert a.cross_agent_dependencies
    for d in a.cross_agent_dependencies:
        consumer, provider = actions[d.consumer_action_id], actions[d.provider_action_id]
        assert consumer.agent_id == d.consumer_agent_id != d.provider_agent_id == provider.agent_id
        if d.via == "parent_action_ids":
            assert d.provider_action_id in consumer.parent_action_ids
        else:  # data provenance: consumer READ the exact version the provider produced
            rid, v = d.via.split("@v")
            snap = next(s for s in sim.workspace.versions(rid) if s.version == int(v))
            assert snap.action_id == d.provider_action_id
            assert any(e.event_type is EventType.RESOURCE_READ and e.action_id == d.consumer_action_id
                       and e.resource_version_after == int(v) for e in sim.monitor.get_workflow_history("wf_001"))
    assert any(d.via.startswith("brief_wf_001@v") and d.provider_agent_id == RESEARCH_ID
               and d.consumer_agent_id == OPERATIONS_ID for d in a.cross_agent_dependencies)


def test_3_resource_modifications_attributed_to_correct_agent():
    sim, _ = run_scenario("legitimate_shared_update_001")
    a = _analysis(sim)
    brief = next(m for m in a.shared_resource_modifications if m.resource_id == "brief_wf_001")
    truth: dict[str, list[int]] = {}
    for v in sim.workspace.versions("brief_wf_001"):
        truth.setdefault(v.agent_id, []).append(v.version)
    assert brief.versions_by_principal == truth
    assert {RESEARCH_ID, COORDINATOR_ID} <= set(brief.agents)


def test_4_history_survives_database_reconnection(tmp_path):
    path = tmp_path / "wf.db"
    sim, _ = run_scenario("distributed_approval_002", path=path)
    # Same builder configuration on both sides (agent roles live in trusted memory, not in SQLite).
    before = run(WorkflowIntelligenceEngine(sim.monitor).analyze_workflow("wf_001")).model_dump(mode="json")
    sim.close()
    db = Database(path, read_only=True)
    try:
        after = run(WorkflowIntelligenceEngine(WorkflowMonitor(db, SteppingClock())).analyze_workflow("wf_001"))
    finally:
        db.close()
    assert after.model_dump(mode="json") == before


def test_5_causal_dependencies_not_invented_from_timestamps(sim, workflow):
    # Two consecutive but unrelated actions by different agents, with no explicit references.
    t = run(sim.coordinator.assign_task(workflow, RESEARCH_ID, "Unrelated task"))
    d = run(sim.operations.prepare_email(workflow, recipient="customer@example.test", subject="Hi", body="Hello."))
    assert t.executed and d.executed
    snap = sim.observation_builder().snapshot(workflow)
    deps = analyze_structure(snap.events, snap.actions, snap.resources, snap.approvals)["cross_agent_dependencies"]
    assert not any({x.consumer_action_id, x.provider_action_id} == {t.action_id, d.action_id} for x in deps)
    graph = sim.graph.build_workflow_graph(workflow)
    assert all(not e.causal for e in graph.edges if e.edge_type.value == "EXECUTED_AFTER")


def test_6_risk_findings_reference_valid_events():
    sim, _ = run_scenario("task_decomposition_001")
    a = _analysis(sim)
    seq = {e.event_id: e.seq for e in sim.monitor.get_workflow_history("wf_001")}
    assert a.findings
    for cp in a.checkpoint_assessments:
        for f in cp.findings:
            assert f.evidence_event_ids
            for e in f.evidence_event_ids:
                assert sim.monitor.get_event(e) is not None
                assert sim.monitor.get_event(e).seq <= cp.checkpoint_seq
    assert all(e in seq for f in a.findings for e in f.evidence_event_ids if seq.get(e) is not None)


def test_44_risk_assessments_reproducibly_reconstructable():
    sim, _ = run_scenario("shared_memory_001")
    assert _analysis(sim).model_dump(mode="json") == _analysis(sim).model_dump(mode="json")
    sim2, _ = run_scenario("shared_memory_001")
    assert _analysis(sim).model_dump(mode="json") == _analysis(sim2).model_dump(mode="json")
