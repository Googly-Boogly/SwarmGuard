"""Workflow monitoring and persistence (spec items 24-27, 30)."""

from __future__ import annotations

from swarmguard.agents.simulator import COORDINATOR_ID, OPERATIONS_ID, RESEARCH_ID, SwarmSimulator
from swarmguard.evaluation.scenarios import run_approval_laundering
from swarmguard.models.decisions import ReasonCode
from swarmguard.models.events import ActorType, EventType
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from tests.conftest import run


def test_24_cross_agent_workflow_events_recorded(sim):
    outcome = run(run_approval_laundering(sim, "wf_l"))
    history = sim.monitor.get_workflow_history("wf_l")
    assert [e.seq for e in history] == sorted(e.seq for e in history)
    assert len({e.event_id for e in history}) == len(history)
    proposers = {e.agent_id for e in history if e.event_type is EventType.ACTION_PROPOSED}
    assert proposers == {COORDINATOR_ID, RESEARCH_ID, OPERATIONS_ID}
    assert {e.actor_type for e in history} >= {ActorType.AGENT, ActorType.HUMAN, ActorType.SYSTEM}
    final = {e.event_type for e in history if e.action_id == outcome.final.action_id}
    assert {EventType.ACTION_PROPOSED, EventType.DECISION_RECORDED, EventType.SECURITY_ALERT} <= final
    assert not any(e.event_type is EventType.EMAIL_SENT_SIMULATED for e in history)


def test_event_parent_references_are_explicit_and_valid(sim):
    run(run_approval_laundering(sim, "wf_l"))
    history = sim.monitor.get_workflow_history("wf_l")
    by_id = {e.event_id: e for e in history}
    for e in history:
        for parent in e.parent_event_ids:
            assert parent in by_id and by_id[parent].seq < e.seq
    granted = next(e for e in history if e.event_type is EventType.APPROVAL_GRANTED)
    assert by_id[granted.parent_event_ids[0]].event_type is EventType.APPROVAL_REQUESTED
    invalidated = next(e for e in history if e.event_type is EventType.APPROVAL_INVALIDATED)
    assert {by_id[p].event_type for p in invalidated.parent_event_ids} == {EventType.APPROVAL_GRANTED,
                                                                         EventType.RESOURCE_MODIFIED}


def test_25_agent_histories_retrievable(sim):
    run(run_approval_laundering(sim, "wf_l"))
    for agent in (COORDINATOR_ID, RESEARCH_ID, OPERATIONS_ID, "human_operator"):
        events = sim.monitor.get_agent_history(agent)
        assert events and all(e.agent_id == agent for e in events)
    assert {e.event_type for e in sim.monitor.get_agent_history("human_operator")} == {EventType.APPROVAL_GRANTED}


def test_26_resource_modification_histories_retrievable(sim):
    outcome = run(run_approval_laundering(sim, "wf_l"))
    events = sim.monitor.get_resource_history(outcome.email_id)
    created = [e for e in events if e.event_type is EventType.RESOURCE_CREATED]
    modified = [e for e in events if e.event_type is EventType.RESOURCE_MODIFIED]
    assert [(e.agent_id, e.resource_version_after) for e in created] == [(OPERATIONS_ID, 1)]
    assert [(e.agent_id, e.resource_version_before, e.resource_version_after) for e in modified] == [(OPERATIONS_ID, 1, 2)]
    related = {a.tool for a in sim.monitor.get_related_actions(outcome.final.action_id)}
    assert {"prepare_email", "request_approval", "modify_email", "read_email_draft"} <= related


def test_27_security_decisions_persisted(sim):
    outcome = run(run_approval_laundering(sim, "wf_l"))
    decisions = sim.monitor.get_decisions("wf_l")
    actions = sim.monitor.get_workflow_actions("wf_l")
    assert {d.action_id for d in decisions} == {a.action_id for a in actions}
    final = sim.monitor.get_decision_for_action(outcome.final.action_id)
    assert (final.reason_code, final.executed, final.execution_status) == (ReasonCode.APPROVAL_MISMATCH, False, "NOT_EXECUTED")
    assert final.details["differing_fields"] == ["resource_version", "recipient"]
    assert "human_approval" not in final.checks_passed and "resource_version" in final.checks_passed


def test_workflow_summary(sim):
    outcome = run(run_approval_laundering(sim, "wf_l"))
    s = sim.monitor.get_workflow_summary("wf_l")
    assert s.workflow.workflow_id == "wf_l" and s.simulated_emails_sent == 0
    assert s.decisions_by_outcome == {"ALLOW": s.action_count - 1, "BLOCK": 1}
    assert s.blocked_actions == [{"action_id": outcome.final.action_id, "agent_id": OPERATIONS_ID,
                                  "tool": "send_email", "reason_code": "APPROVAL_MISMATCH"}]
    assert {a["status"] for a in s.approvals} == {"INVALIDATED"}
    assert sim.monitor.get_workflow_summary("nope") is None


def test_30_events_survive_restart_and_reconnection(tmp_path):
    path = tmp_path / "persist.db"
    sim = SwarmSimulator(path)
    outcome = run(run_approval_laundering(sim, "wf_p"))
    snapshot = [e.model_dump() for e in sim.monitor.get_workflow_history("wf_p")]
    sim.close()

    ro = Database(path, read_only=True)
    monitor = WorkflowMonitor(ro, sim.clock)
    assert [e.model_dump() for e in monitor.get_workflow_history("wf_p")] == snapshot
    assert monitor.get_decision_for_action(outcome.final.action_id).reason_code is ReasonCode.APPROVAL_MISMATCH
    ro.close()

    reopened = SwarmSimulator(path)  # a fresh "application restart" on the same file
    assert len(reopened.monitor.get_workflow_history("wf_p")) == len(snapshot)
    assert reopened.workspace.get(outcome.email_id).version == 2
    reopened.close()


def test_live_approvals_after_restart_require_the_same_signing_key(tmp_path):
    from tests.conftest import approve, make_email, send

    path, key = tmp_path / "keys.db", b"s" * 32
    sim = SwarmSimulator(path, approval_signing_key=key)
    sim.start_workflow("wf", "t")
    email_id = make_email(sim, "wf")
    approval_id = approve(sim, "wf", email_id)
    sim.close()

    other_key = SwarmSimulator(path)  # random key: previously signed approvals fail closed
    assert send(other_key, "wf", email_id, 1, approval_id).reason_code is ReasonCode.APPROVAL_INTEGRITY_FAILURE
    other_key.close()
    same_key = SwarmSimulator(path, approval_signing_key=key)
    assert send(same_key, "wf", email_id, 1, approval_id).executed
    same_key.close()
