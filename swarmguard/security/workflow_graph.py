"""Workflow dependency graph built from persisted SQLite records.

Causal edges come only from explicit references stored at write time:

* ``parent_action_ids`` on actions                      -> DEPENDS_ON
* ``resource_version`` on requests and read/write events -> READS_FROM / MODIFIES / SUPERSEDES
* approval records (requesting action, bound version,
  approver, consumer) and approval events                -> REQUESTS_APPROVAL / BINDS_TO / APPROVED_BY /
                                                            INVALIDATED_BY / CITES_APPROVAL / CONSUMED_BY
* decision records                                       -> DECIDED / RESULTED_IN
* task content (assigned_by / assignee)                  -> ASSIGNED_BY / ASSIGNED_TO

``EXECUTED_AFTER`` is the only chronological edge and is marked ``causal=False``.
Nothing is inferred from timestamps alone. Every node references a persisted
record via ``ref_table`` / ``ref_id``.
"""

from __future__ import annotations

from collections import deque

from swarmguard.models.events import EventType
from swarmguard.models.workflows import (
    ActionDependencies,
    ApprovalChain,
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
    ResourceVersion,
    WorkflowGraph,
)
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.repositories import Repositories


def action_node(action_id: str) -> str:
    return f"action:{action_id}"


def version_node(resource_id: str, version: int) -> str:
    return f"resource:{resource_id}@v{version}"


def approval_node(approval_id: str) -> str:
    return f"approval:{approval_id}"


class WorkflowGraphService:
    def __init__(self, monitor: WorkflowMonitor) -> None:
        self._monitor = monitor
        self._repos: Repositories = monitor.repos

    # ------------------------------------------------------------------ graph

    def build_workflow_graph(self, workflow_id: str) -> WorkflowGraph | None:
        if self._monitor.get_workflow(workflow_id) is None:
            return None
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        def add_node(node: GraphNode) -> None:
            nodes.setdefault(node.node_id, node)

        def add_edge(source: str, target: str, edge_type: EdgeType, *, causal: bool = True,
                     evidence: list[str] | None = None) -> None:
            if source in nodes and target in nodes:
                edges.append(GraphEdge(source=source, target=target, edge_type=edge_type, causal=causal,
                                       evidence_event_ids=evidence or []))

        def principal(principal_id: str, kind: NodeType) -> str:
            node_id = f"{kind.value.lower()}:{principal_id}"
            add_node(GraphNode(node_id=node_id, node_type=kind, label=principal_id, ref_table=None, ref_id=principal_id))
            return node_id

        def resource_version(resource_id: str, version: int) -> str | None:
            node_id = version_node(resource_id, version)
            if node_id not in nodes:
                snap = self._version(resource_id, version)
                if snap is None:
                    return None
                add_node(GraphNode(
                    node_id=node_id, node_type=NodeType.RESOURCE_VERSION, label=f"{resource_id} v{version}",
                    ref_table="resource_versions", ref_id=f"{resource_id}:{version}",
                    event_ids=[snap.event_id] if snap.event_id else [],
                    attributes={"resource_id": resource_id, "version": version, "modified_by": snap.agent_id,
                                "action_id": snap.action_id, "changes": snap.changes},
                ))
                if version > 1 and resource_version(resource_id, version - 1):
                    add_edge(node_id, version_node(resource_id, version - 1), EdgeType.SUPERSEDES,
                             evidence=[snap.event_id] if snap.event_id else [])
            return node_id

        actions = self._monitor.get_workflow_actions(workflow_id)
        events = self._monitor.get_workflow_history(workflow_id)
        decisions = {d.action_id: d for d in self._monitor.get_decisions(workflow_id)}
        approvals = self._repos.approvals.list(workflow_id=workflow_id)

        # Actions, proposers, decisions, outcomes
        for a in actions:
            ev = [e.event_id for e in events if e.action_id == a.action_id]
            add_node(GraphNode(
                node_id=action_node(a.action_id), node_type=NodeType.ACTION, label=f"{a.agent_id}: {a.tool}",
                ref_table="actions", ref_id=a.action_id, event_ids=ev,
                attributes={"tool": a.tool, "agent_id": a.agent_id, "resource_id": a.resource_id,
                            "resource_version": a.resource_version, "approval_id": a.approval_id,
                            "created_at": a.created_at.isoformat()},
            ))
            add_edge(action_node(a.action_id), principal(a.agent_id, NodeType.AGENT), EdgeType.PROPOSED_BY)
            d = decisions.get(a.action_id)
            if d is not None:
                dn = f"decision:{d.decision_id}"
                add_node(GraphNode(node_id=dn, node_type=NodeType.DECISION, label=f"{d.decision.value} [{d.reason_code.value}]",
                                   ref_table="decisions", ref_id=d.decision_id,
                                   attributes={"decision": d.decision.value, "reason_code": d.reason_code.value,
                                               "explanation": d.explanation}))
                add_edge(action_node(a.action_id), dn, EdgeType.DECIDED)
                outcome_events = [e.event_id for e in events if e.action_id == a.action_id and e.event_type in (
                    EventType.ACTION_EXECUTED, EventType.ACTION_EXECUTION_FAILED, EventType.EMAIL_SENT_SIMULATED,
                    EventType.SECURITY_ALERT)]
                xn = f"execution:{a.action_id}"
                add_node(GraphNode(node_id=xn, node_type=NodeType.EXECUTION,
                                   label="EXECUTED" if d.executed else f"NOT EXECUTED ({d.execution_status})",
                                   ref_table="decisions", ref_id=d.decision_id, event_ids=outcome_events,
                                   attributes={"executed": d.executed, "status": d.execution_status}))
                add_edge(dn, xn, EdgeType.RESULTED_IN, evidence=outcome_events)

        for a in actions:
            for parent in a.parent_action_ids:
                add_edge(action_node(a.action_id), action_node(parent), EdgeType.DEPENDS_ON)

        # Resource reads/writes (explicit versions on events)
        for e in events:
            if e.action_id is None or e.resource_id is None:
                continue
            if e.event_type is EventType.RESOURCE_READ and e.resource_version_after:
                if resource_version(e.resource_id, e.resource_version_after):
                    add_edge(action_node(e.action_id), version_node(e.resource_id, e.resource_version_after),
                             EdgeType.READS_FROM, evidence=[e.event_id])
            elif e.event_type in (EventType.RESOURCE_CREATED, EventType.RESOURCE_MODIFIED) and e.resource_version_after:
                if resource_version(e.resource_id, e.resource_version_after):
                    add_edge(action_node(e.action_id), version_node(e.resource_id, e.resource_version_after),
                             EdgeType.MODIFIES, evidence=[e.event_id])
        # Requests that cite an expected version read from that exact version.
        for a in actions:
            if a.resource_id and a.resource_version and resource_version(a.resource_id, a.resource_version):
                target = version_node(a.resource_id, a.resource_version)
                if not any(x.source == action_node(a.action_id) and x.target == target for x in edges):
                    add_edge(action_node(a.action_id), target, EdgeType.READS_FROM)

        # Task assignment
        for e in events:
            if e.event_type is EventType.TASK_ASSIGNED and e.resource_id:
                snap = self._version(e.resource_id, 1)
                vn = resource_version(e.resource_id, 1)
                if snap and vn:
                    add_edge(vn, principal(str(snap.content.get("assigned_by")), NodeType.AGENT), EdgeType.ASSIGNED_BY,
                             evidence=[e.event_id])
                    add_edge(vn, principal(str(snap.content.get("assignee")), NodeType.AGENT), EdgeType.ASSIGNED_TO,
                             evidence=[e.event_id])

        # Approvals
        for ap in approvals:
            an = approval_node(ap.approval_id)
            ap_events = [e.event_id for e in events if e.approval_id == ap.approval_id]
            add_node(GraphNode(node_id=an, node_type=NodeType.APPROVAL, label=f"{ap.approval_id} ({ap.status.value})",
                               ref_table="approvals", ref_id=ap.approval_id, event_ids=ap_events,
                               attributes={"status": ap.status.value, "operation": ap.operation,
                                           "resource_id": ap.resource_id, "resource_version": ap.resource_version,
                                           "approved_parameters": ap.approved_parameters,
                                           "operation_hash": ap.operation_hash}))
            if ap.requesting_action_id:
                add_edge(action_node(ap.requesting_action_id), an, EdgeType.REQUESTS_APPROVAL)
            if resource_version(ap.resource_id, ap.resource_version):
                add_edge(an, version_node(ap.resource_id, ap.resource_version), EdgeType.BINDS_TO)
            if ap.approver_id:
                granted = [e.event_id for e in events if e.approval_id == ap.approval_id
                           and e.event_type is EventType.APPROVAL_GRANTED]
                rejected = [e.event_id for e in events if e.approval_id == ap.approval_id
                            and e.event_type is EventType.APPROVAL_REJECTED]
                human = principal(ap.approver_id, NodeType.HUMAN)
                if granted:
                    add_edge(an, human, EdgeType.APPROVED_BY, evidence=granted)
                if rejected:
                    add_edge(an, human, EdgeType.REJECTED_BY, evidence=rejected)
            for e in events:
                if e.approval_id == ap.approval_id and e.event_type is EventType.APPROVAL_INVALIDATED and e.action_id:
                    add_edge(an, action_node(e.action_id), EdgeType.INVALIDATED_BY, evidence=[e.event_id])
            if ap.consumed_by_action_id:
                add_edge(an, action_node(ap.consumed_by_action_id), EdgeType.CONSUMED_BY)
        for a in actions:
            if a.approval_id and approval_node(a.approval_id) in nodes:
                add_edge(action_node(a.action_id), approval_node(a.approval_id), EdgeType.CITES_APPROVAL)

        # Chronology (explicitly NOT causal)
        for prev, cur in zip(actions, actions[1:], strict=False):
            add_edge(action_node(cur.action_id), action_node(prev.action_id), EdgeType.EXECUTED_AFTER, causal=False)

        return WorkflowGraph(workflow_id=workflow_id, nodes=list(nodes.values()), edges=edges)

    # ------------------------------------------------------------------ queries

    def get_action_dependencies(self, action_id: str) -> ActionDependencies | None:
        action = self._monitor.get_action(action_id)
        if action is None:
            return None
        graph = self.build_workflow_graph(action.workflow_id)
        assert graph is not None
        out: dict[str, list[GraphEdge]] = {}
        producers: dict[str, str] = {}
        requesters: dict[str, str] = {}
        for e in graph.edges:
            if not e.causal:
                continue
            out.setdefault(e.source, []).append(e)
            if e.edge_type is EdgeType.MODIFIES:
                producers[e.target] = e.source
            if e.edge_type is EdgeType.REQUESTS_APPROVAL:
                requesters[e.target] = e.source

        start = action_node(action_id)
        seen_actions: set[str] = set()
        reads: set[str] = set()
        providers: set[str] = set()
        approvals: set[str] = set()
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for edge in out.get(current, []):
                nxt: str | None = None
                if edge.edge_type is EdgeType.DEPENDS_ON:
                    nxt = edge.target
                elif edge.edge_type is EdgeType.READS_FROM:
                    reads.add(edge.target)
                    nxt = producers.get(edge.target)
                    if nxt:
                        providers.add(nxt)
                elif edge.edge_type is EdgeType.CITES_APPROVAL:
                    approvals.add(edge.target)
                    nxt = requesters.get(edge.target)
                if nxt and nxt != start and nxt not in seen_actions:
                    seen_actions.add(nxt)
                    queue.append(nxt)

        order = {n.node_id: n.attributes.get("created_at", "") for n in graph.nodes}
        strip = lambda ids: sorted((i.split(":", 1)[1] for i in ids), key=lambda x: (order.get(action_node(x), ""), x))  # noqa: E731
        return ActionDependencies(
            action_id=action_id,
            direct_parents=list(action.parent_action_ids),
            ancestors=strip(seen_actions),
            resources_read=sorted(r.split(":", 1)[1] for r in reads),
            data_providers=strip(providers),
            approvals=sorted(a.split(":", 1)[1] for a in approvals),
        )

    def get_resource_modification_chain(self, resource_id: str) -> list[ResourceVersion]:
        return self._repos.resources.versions(resource_id)

    def get_approval_dependency_chain(self, approval_id: str) -> ApprovalChain | None:
        ap = self._repos.approvals.get(approval_id)
        if ap is None:
            return None
        deps = self.get_action_dependencies(ap.requesting_action_id) if ap.requesting_action_id else None
        invalidators = sorted({e.action_id for e in self._monitor.find_events(
            approval_id=approval_id, event_type=EventType.APPROVAL_INVALIDATED) if e.action_id})
        citing = []
        for a in self._monitor.get_workflow_actions(ap.workflow_id):
            if a.approval_id == approval_id:
                d = self._monitor.get_decision_for_action(a.action_id)
                citing.append({"action_id": a.action_id, "agent_id": a.agent_id, "tool": a.tool,
                               "decision": d.decision.value if d else "UNDECIDED",
                               "reason_code": d.reason_code.value if d else ""})
        return ApprovalChain(
            approval_id=approval_id, status=ap.status.value, requested_by_action=ap.requesting_action_id,
            requested_by_agent=ap.requested_by,
            request_ancestors=(deps.ancestors if deps else []) + ([ap.requesting_action_id] if ap.requesting_action_id else []),
            bound_resource_version=f"{ap.resource_id}@v{ap.resource_version}", approver_id=ap.approver_id,
            invalidated_by_actions=invalidators, citing_actions=citing, consumed_by_action=ap.consumed_by_action_id,
        )

    def render_causal_chain(self, action_id: str) -> list[str]:
        """Human-readable steps behind an action. Links come from explicit references;
        the steps are listed in recorded order only for readability."""
        deps = self.get_action_dependencies(action_id)
        if deps is None:
            return []
        involved = set(deps.ancestors) | {action_id}
        lines: list[str] = []
        action = self._monitor.get_action(action_id)
        assert action is not None
        for e in self._monitor.get_workflow_history(action.workflow_id):
            relevant_action = e.action_id in involved and e.event_type in (
                EventType.TASK_ASSIGNED, EventType.RESOURCE_READ, EventType.RESOURCE_CREATED,
                EventType.RESOURCE_MODIFIED, EventType.APPROVAL_REQUESTED, EventType.DECISION_RECORDED)
            relevant_approval = e.approval_id in deps.approvals and e.event_type in (
                EventType.APPROVAL_GRANTED, EventType.APPROVAL_REJECTED, EventType.APPROVAL_INVALIDATED)
            if e.event_type is EventType.DECISION_RECORDED and e.action_id != action_id:
                continue
            if relevant_action or relevant_approval:
                lines.append(f"[{e.event_id}] {e.summary}")
        return lines

    def _version(self, resource_id: str, version: int) -> ResourceVersion | None:
        return next((v for v in self._repos.resources.versions(resource_id) if v.version == version), None)
