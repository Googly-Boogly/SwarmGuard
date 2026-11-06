"""Cross-Agent Workflow Intelligence Engine and the trusted Workflow Guard.

``WorkflowIntelligenceEngine`` analyzes relationships between actions of different
agents from the persisted audit log:

* participating agents and principals, resources accessed;
* explicit cross-agent dependencies, from ``parent_action_ids`` and from data
  provenance (an action read ``r@vN`` and another agent's action produced ``r@vN``);
* shared-resource modifications (versions by principal) and approval relationships;
* safety findings at every outbound / approval-citing checkpoint, each evaluated on a
  snapshot cut off at that checkpoint, so no future events are used.

Dependencies come only from explicit references. Timestamps and ordering are never
used to invent causal links. Findings are labelled ``EXPLICIT`` or ``HEURISTIC``.

``WorkflowGuard`` is the optional **enforcement mode**. The capability gateway calls it
(trusted, deterministic) after the policy engine returns ALLOW for a guarded tool. Its
alert can turn that ALLOW into ESCALATE through the ``DecisionEngine``, and nothing else:
it cannot relax a BLOCK or ESCALATE, approve, or execute.
"""

from __future__ import annotations

from collections.abc import Iterable

from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import WorkflowContext
from swarmguard.models.events import ActorType, EventType, WorkflowEvent
from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.risk import (
    ApprovalRelationship,
    ApprovalView,
    CrossAgentDependency,
    DataFlowTrace,
    MonitorContext,
    ObservationWindow,
    PatternFinding,
    ResourceSnapshot,
    SafetyAssessment,
    SharedResourceModification,
    WorkflowRiskAssessment,
)
from swarmguard.monitoring.base import SafetyMonitor
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from swarmguard.monitoring.workflow_context import ObservationBuilder
from swarmguard.security.invariants import OUTBOUND_TOOLS
from swarmguard.security.pattern_detector import SYSTEM_PREFIX
from swarmguard.security.workflow_monitor import WorkflowMonitor

_UNC_RANK = {Uncertainty.LOW: 0, Uncertainty.MEDIUM: 1, Uncertainty.HIGH: 2}
_REC_RANK = {Recommendation.ALLOW: 0, Recommendation.ESCALATE: 1, Recommendation.BLOCK: 2}


def _is_agent(principal: str, actor_types: dict[str, ActorType]) -> bool:
    return actor_types.get(principal) is ActorType.AGENT and not principal.startswith(SYSTEM_PREFIX)


def analyze_structure(events: list[WorkflowEvent], actions: list[ActionRequest], resources: list[ResourceSnapshot],
                      approvals: list[ApprovalView]) -> dict:
    """Explicit-reference structural analysis shared by workflow-level and action-level entry points."""
    actor_types = {e.agent_id: e.actor_type for e in events}
    by_action = {a.action_id: a for a in actions}
    proposal_event = {e.action_id: e.event_id for e in events if e.event_type is EventType.ACTION_PROPOSED}
    producer = {(r.resource_id, r.version): r for r in resources}

    deps: list[CrossAgentDependency] = []
    for a in actions:
        for p in a.parent_action_ids:
            parent = by_action.get(p)
            if parent is not None and parent.agent_id != a.agent_id:
                deps.append(CrossAgentDependency(
                    consumer_action_id=a.action_id, consumer_agent_id=a.agent_id, provider_action_id=p,
                    provider_agent_id=parent.agent_id, via="parent_action_ids",
                    evidence_event_ids=[i for i in (proposal_event.get(a.action_id),) if i]))
    for e in events:
        if e.event_type is not EventType.RESOURCE_READ or not e.action_id or not e.resource_id:
            continue
        snap = producer.get((e.resource_id, e.resource_version_after or 0))
        consumer = by_action.get(e.action_id)
        if snap is None or consumer is None or snap.action_id is None:
            continue
        provider = by_action.get(snap.action_id)
        if provider is not None and provider.agent_id != consumer.agent_id:
            deps.append(CrossAgentDependency(
                consumer_action_id=consumer.action_id, consumer_agent_id=consumer.agent_id,
                provider_action_id=provider.action_id, provider_agent_id=provider.agent_id,
                via=f"{e.resource_id}@v{snap.version}",
                evidence_event_ids=[i for i in (snap.event_id, e.event_id) if i]))

    shared: list[SharedResourceModification] = []
    by_resource: dict[str, list[ResourceSnapshot]] = {}
    for r in resources:
        by_resource.setdefault(r.resource_id, []).append(r)
    for rid, snaps in sorted(by_resource.items()):
        versions: dict[str, list[int]] = {}
        for s in sorted(snaps, key=lambda x: x.version):
            versions.setdefault(s.agent_id, []).append(s.version)
        if len(versions) < 2:
            continue
        readers = sorted({e.agent_id for e in events if e.event_type is EventType.RESOURCE_READ and e.resource_id == rid})
        shared.append(SharedResourceModification(
            resource_id=rid, resource_type=snaps[0].resource_type, versions_by_principal=versions,
            agents=sorted(p for p in versions if not p.startswith(SYSTEM_PREFIX)), readers=readers,
            evidence_event_ids=[s.event_id for s in sorted(snaps, key=lambda x: x.version) if s.event_id]))

    rels: list[ApprovalRelationship] = []
    for ap in approvals:
        mods = [e for e in events if e.event_type is EventType.RESOURCE_MODIFIED and e.resource_id == ap.resource_id
                and (e.resource_version_before or 0) >= ap.resource_version]
        rels.append(ApprovalRelationship(
            approval_id=ap.approval_id, status=ap.status, bound_resource_version=f"{ap.resource_id}@v{ap.resource_version}",
            requested_by=ap.requested_by, approver_id=ap.approver_id,
            citing_action_ids=[a.action_id for a in actions if a.approval_id == ap.approval_id],
            modified_after_binding_by=sorted({e.agent_id for e in mods}),
            evidence_event_ids=[*ap.evidence_event_ids, *(e.event_id for e in mods)]))

    principals = sorted({e.agent_id for e in events})
    return {
        "participating_agents": sorted(p for p in principals if _is_agent(p, actor_types)),
        "principals": principals,
        "resources_accessed": sorted({e.resource_id for e in events if e.resource_id and e.event_type in (
            EventType.RESOURCE_READ, EventType.RESOURCE_CREATED, EventType.RESOURCE_MODIFIED)}),
        "cross_agent_dependencies": deps,
        "shared_resource_modifications": shared,
        "approval_relationships": rels,
    }


def _dedupe(findings: Iterable[PatternFinding]) -> list[PatternFinding]:
    seen: dict[tuple, PatternFinding] = {}
    for f in findings:
        seen.setdefault((f.pattern_id, f.invariant_id, f.explanation), f)
    return list(seen.values())


def combine_assessments(assessments: list[SafetyAssessment]) -> tuple[RiskLevel, Recommendation, Uncertainty, str]:
    if not assessments:
        return RiskLevel.LOW, Recommendation.ALLOW, Uncertainty.HIGH, "No outbound or approval-citing checkpoints."
    risk = max((a.risk_level for a in assessments), key=lambda r: r.rank)
    rec = max((a.recommendation for a in assessments), key=lambda r: _REC_RANK[r])
    unc = max((a.uncertainty for a in assessments), key=lambda u: _UNC_RANK[u])
    alerts = [a for a in assessments if a.recommendation is not Recommendation.ALLOW]
    text = (f"{len(alerts)} of {len(assessments)} checkpoint(s) raised an alert; first at {alerts[0].action_id}: "
            f"{alerts[0].explanation}" if alerts else
            f"No alert at any of {len(assessments)} checkpoint(s). {assessments[-1].explanation}")
    return risk, rec, unc, text[:4000]


class WorkflowIntelligenceEngine:
    def __init__(self, monitor: WorkflowMonitor, *, builder: ObservationBuilder | None = None,
                 safety_monitor: SafetyMonitor | None = None) -> None:
        self._monitor = monitor
        self._builder = builder or ObservationBuilder(monitor)
        self._safety = safety_monitor or GlobalWorkflowMonitor()

    @property
    def builder(self) -> ObservationBuilder:
        return self._builder

    def relevant_checkpoints(self, workflow_id: str) -> list[tuple[str, int]]:
        out = []
        for action_id, seq in self._builder.checkpoints(workflow_id):
            action = self._monitor.get_action(action_id)
            if action is not None and (action.tool in OUTBOUND_TOOLS or action.approval_id):
                out.append((action_id, seq))
        return out

    async def assess_checkpoint(self, action_id: str) -> SafetyAssessment:
        snap = self._builder.snapshot_for_action(action_id)
        ctx = self._builder.build(self._safety.window, snap, action_id)
        return await self._safety.evaluate(ctx.action, ctx)

    async def analyze_workflow(self, workflow_id: str) -> WorkflowRiskAssessment:
        snap = self._builder.snapshot(workflow_id)
        structure = analyze_structure(snap.events, snap.actions, snap.resources, snap.approvals)
        assessments = [await self.assess_checkpoint(a) for a, _ in self.relevant_checkpoints(workflow_id)]
        return self._assemble(workflow_id, snap.cutoff_seq, structure, assessments)

    async def analyze_proposed_action(self, action: ActionRequest, workflow_context: MonitorContext
                                      ) -> WorkflowRiskAssessment:
        ctx = workflow_context
        if ctx.window is not self._safety.window:
            raise ValueError(f"engine monitor expects {self._safety.window.value} context")
        structure = analyze_structure(ctx.events, [*ctx.prior_actions, ctx.action], ctx.resources, ctx.approvals)
        assessment = await self._safety.evaluate(action, ctx)
        return self._assemble(ctx.workflow_id, ctx.checkpoint_seq, structure, [assessment])

    async def enrich_ethics_context(self, context: WorkflowContext) -> WorkflowContext:
        """Attach structured workflow analysis for (future, Stage 3) ethics monitors. Advisory data only."""
        snap = self._builder.snapshot_for_action(context.action.action_id)
        ctx = self._builder.build(ObservationWindow.GLOBAL, snap, context.action.action_id)
        analysis = await self.analyze_proposed_action(context.action, ctx)
        return context.model_copy(update={"workflow_analysis": analysis})

    @staticmethod
    def _assemble(workflow_id: str, through_seq: int, structure: dict,
                  assessments: list[SafetyAssessment]) -> WorkflowRiskAssessment:
        risk, rec, unc, text = combine_assessments(assessments)
        flows: dict[tuple, DataFlowTrace] = {}
        for a in assessments:
            for f in a.data_flows:
                flows.setdefault((f.source_resource_id, f.sink_action_id), f)
        return WorkflowRiskAssessment(
            workflow_id=workflow_id, analyzed_through_seq=through_seq, **structure,
            data_flows=list(flows.values()), checkpoint_assessments=assessments,
            findings=_dedupe(f for a in assessments for f in a.findings),
            risk_level=risk, recommendation=rec, uncertainty=unc, explanation=text,
        )


class WorkflowGuard:
    """Trusted enforcement adapter used by the capability gateway (enforcement mode only)."""

    def __init__(self, builder: ObservationBuilder, monitor: SafetyMonitor,
                 tools: frozenset[str] = frozenset({"send_email"})) -> None:
        self._builder = builder
        self._monitor = monitor
        self.tools = tools

    @property
    def monitor_id(self) -> str:
        return self._monitor.monitor_id

    async def assess(self, request: ActionRequest) -> SafetyAssessment:
        snap = self._builder.snapshot_for_action(request.action_id)
        ctx = self._builder.build(self._monitor.window, snap, request.action_id)
        return await self._monitor.evaluate(request, ctx)
