"""Workflow dependency graph (spec items 28-29 and the graph acceptance sequence)."""

from __future__ import annotations

import json

from swarmguard.evaluation.scenarios import run_approval_laundering, run_authorized_email
from swarmguard.models.workflows import EdgeType, NodeType, WorkflowGraph
from tests.conftest import run


def _laundering(sim):
    return run(run_approval_laundering(sim, "wf_g"))


def test_28_action_dependencies_reconstructable(sim):
    o = _laundering(sim)
    deps = sim.graph.get_action_dependencies(o.final.action_id)
    actions = {a.action_id: a for a in sim.monitor.get_workflow_actions("wf_g")}
    tools = [actions[a].tool for a in deps.ancestors]
    for needed in ("assign_task", "read_customer_record", "update_shared_document", "prepare_email",
                   "request_approval", "modify_email", "read_email_draft"):
        assert needed in tools, needed
    assert f"{o.email_id}@v1" in deps.resources_read and f"{o.email_id}@v2" in deps.resources_read
    assert deps.approvals == [o.approval_id]
    agents = {actions[a].agent_id for a in deps.data_providers}
    assert agents == {"research_agent", "operations_agent"}  # cross-agent data provenance
    assert sim.graph.get_action_dependencies("action_999") is None


def test_29_graph_references_are_valid(sim):
    _laundering(sim)
    g = sim.graph.build_workflow_graph("wf_g")
    ids = {n.node_id for n in g.nodes}
    assert all(e.source in ids and e.target in ids for e in g.edges)
    lookups = {
        "actions": lambda i: sim.monitor.get_action(i),
        "decisions": lambda i: sim.repos.decisions.get(i),
        "approvals": lambda i: sim.approvals.get(i),
        "resource_versions": lambda i: sim.workspace.get_version(i.split(":")[0], int(i.split(":")[1])),
    }
    for n in g.nodes:
        if n.ref_table:
            assert lookups[n.ref_table](n.ref_id) is not None, n
        for ev in n.event_ids:
            assert sim.monitor.get_event(ev) is not None
    for e in g.edges:
        for ev in e.evidence_event_ids:
            assert sim.monitor.get_event(ev) is not None
    chrono = g.edges_of(EdgeType.EXECUTED_AFTER)
    assert chrono and all(not e.causal for e in chrono)
    assert all(e.causal for e in g.edges if e.edge_type is not EdgeType.EXECUTED_AFTER)
    assert WorkflowGraph.model_validate(json.loads(g.model_dump_json())) == g  # serializable


def test_graph_reveals_acceptance_sequence(sim):
    o = _laundering(sim)
    g = sim.graph.build_workflow_graph("wf_g")
    node = {n.node_id: n for n in g.nodes}

    def edges(t, src=None, dst=None):
        return [e for e in g.edges_of(t) if (src is None or e.source == src) and (dst is None or e.target == dst)]

    # Coordinator assigns task
    assert edges(EdgeType.ASSIGNED_BY, dst="agent:coordinator")
    # Research Agent prepares data (modifies the brief) ...
    research_mods = [e for e in edges(EdgeType.MODIFIES) if node[e.source].attributes["agent_id"] == "research_agent"]
    assert research_mods
    # ... Operations drafts email v1
    v1, v2 = f"resource:{o.email_id}@v1", f"resource:{o.email_id}@v2"
    (draft,) = edges(EdgeType.MODIFIES, dst=v1)
    assert node[draft.source].attributes["tool"] == "prepare_email"
    # Human approves version 1
    ap = f"approval:{o.approval_id}"
    assert edges(EdgeType.BINDS_TO, src=ap, dst=v1) and edges(EdgeType.APPROVED_BY, src=ap, dst="human:human_operator")
    # Agent modifies email to version 2 (which supersedes v1) and invalidates the approval
    (mod,) = edges(EdgeType.MODIFIES, dst=v2)
    assert edges(EdgeType.SUPERSEDES, src=v2, dst=v1) and edges(EdgeType.INVALIDATED_BY, src=ap, dst=mod.source)
    # Operations requests send citing the approval, and the gateway rejects it
    send = f"action:{o.final.action_id}"
    assert edges(EdgeType.CITES_APPROVAL, src=send, dst=ap) and edges(EdgeType.READS_FROM, src=send, dst=v2)
    (decided,) = edges(EdgeType.DECIDED, src=send)
    assert node[decided.target].attributes["reason_code"] == "APPROVAL_MISMATCH"
    (outcome,) = edges(EdgeType.RESULTED_IN, src=decided.target)
    assert node[outcome.target].node_type is NodeType.EXECUTION and node[outcome.target].attributes["executed"] is False


def test_resource_and_approval_chains(sim):
    o = _laundering(sim)
    chain = sim.graph.get_resource_modification_chain(o.email_id)
    assert [(v.version, v.content["recipient"]) for v in chain] == [(1, "customer@example.test"), (2, "other@example.test")]
    ap = sim.graph.get_approval_dependency_chain(o.approval_id)
    assert ap.status == "INVALIDATED" and ap.bound_resource_version == f"{o.email_id}@v1"
    assert ap.approver_id == "human_operator" and len(ap.invalidated_by_actions) == 1
    assert ap.citing_actions == [{"action_id": o.final.action_id, "agent_id": "operations_agent", "tool": "send_email",
                                  "decision": "BLOCK", "reason_code": "APPROVAL_MISMATCH"}]
    good = run(run_authorized_email(sim, "wf_ok"))
    assert sim.graph.get_approval_dependency_chain(good.approval_id).consumed_by_action == good.final.action_id
    assert sim.graph.build_workflow_graph("nope") is None
