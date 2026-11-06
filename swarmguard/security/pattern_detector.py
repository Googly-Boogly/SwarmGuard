"""Deterministic pattern detectors over a ``MonitorContext``.

Every detector inspects only the context it is given (events, actions, resource
snapshots, approval views, invariant configuration). The Local and Global
monitors run *exactly these detectors*. The only experimental difference between
them is how much of the workflow the context contains.

Detectors report evidence. They do not decide what is unsafe: apart from
``UNVERIFIED_SENSITIVE_CONTENT``, every base finding is non-actionable.
``risk_analyzer`` turns explicit invariant violations into actionable findings.
Accessing the same resource, or modifying shared state, is ordinary collaboration
and is never treated as a violation on its own.

Limitations (documented, deliberate):
* Data-flow tracing matches synthetic field values as case-insensitive
  substrings. A paraphrased or partially redacted value ("Riley P.") is NOT
  detected. That is a heuristic, and findings that rely on it are labelled
  ``HEURISTIC``.
* Nothing here infers intent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from swarmguard.models.events import EventType, WorkflowEvent
from swarmguard.models.levels import RiskLevel
from swarmguard.models.risk import (
    DataFlowTrace,
    EvidenceBasis,
    FlowHop,
    InvariantKind,
    MonitorContext,
    PatternFinding,
    PatternId,
    ResourceSnapshot,
)
from swarmguard.models.approvals import ApprovalStatus
from swarmguard.models.workflows import ResourceType

MIN_MATCH_LENGTH = 4
_UNMATCHABLE = {"none", "n/a", "unknown", ""}
SYSTEM_PREFIX = "swarmguard."


@dataclass(frozen=True)
class Governance:
    """Union of the data-governance settings declared by the context's invariants."""

    protected_fields: tuple[str, ...]
    quasi_identifier_fields: tuple[str, ...]
    recipient_fields: tuple[str, ...]
    consent_field: str | None
    sensitive_patterns: tuple[str, ...]
    max_quasi_identifiers: int
    outbound_tools: frozenset[str]
    record_types: frozenset[ResourceType]


def governance(ctx: MonitorContext) -> Governance:
    inv = ctx.invariants

    def union(attr: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(x for i in inv for x in getattr(i, attr)))

    data_kinds = {InvariantKind.RECIPIENT_AUTHORIZATION, InvariantKind.DATA_PURPOSE,
                  InvariantKind.CUMULATIVE_DISCLOSURE, InvariantKind.PROTECTED_INFORMATION_FLOW}
    cumulative = [i for i in inv if i.kind is InvariantKind.CUMULATIVE_DISCLOSURE]
    return Governance(
        protected_fields=union("protected_fields"), quasi_identifier_fields=union("quasi_identifier_fields"),
        recipient_fields=union("authorized_recipient_fields"),
        consent_field=next((i.consent_field for i in inv if i.consent_field), None),
        sensitive_patterns=union("sensitive_patterns"),
        max_quasi_identifiers=min((i.max_quasi_identifiers for i in cumulative), default=1),
        outbound_tools=frozenset(t for i in inv if i.kind in data_kinds for t in i.applies_to_tools),
        record_types=frozenset(t for i in inv for t in i.protected_resource_types),
    )


@dataclass(frozen=True)
class OutboundMessage:
    action_id: str
    agent_id: str
    tool: str
    recipient: str | None
    text: str
    evidence_event_ids: tuple[str, ...]


@dataclass
class DetectionResult:
    findings: list[PatternFinding] = field(default_factory=list)
    flows: list[DataFlowTrace] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    outbound: OutboundMessage | None = None
    flow_findings: dict[str, PatternFinding] = field(default_factory=dict)  # source record -> its flow finding

    def note(self, text: str) -> None:
        if text not in self.missing_information:
            self.missing_information.append(text)


# --------------------------------------------------------------------------- context helpers


def proposal_event(ctx: MonitorContext) -> WorkflowEvent | None:
    return next((e for e in ctx.events if e.action_id == ctx.action.action_id
                 and e.event_type is EventType.ACTION_PROPOSED), None)


def _snapshot(ctx: MonitorContext, resource_id: str | None, version: int | None) -> ResourceSnapshot | None:
    if resource_id is None:
        return None
    versions = [r for r in ctx.resources if r.resource_id == resource_id]
    if version is not None:
        return next((r for r in versions if r.version == version), None)
    return max(versions, key=lambda r: r.version, default=None)


def _norm(address: Any) -> str | None:
    return address.strip().lower() if isinstance(address, str) and address.strip() else None


def _text(content: dict[str, Any]) -> str:
    return "\n".join(str(content.get(k, "")) for k in ("subject", "body"))


def _flatten(content: dict[str, Any]) -> str:
    return "\n".join(str(v) for v in content.values())


def _matchable(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v if len(v) >= MIN_MATCH_LENGTH and v.casefold() not in _UNMATCHABLE else None


def _contains(text: str, value: str) -> bool:
    return value.casefold() in text.casefold()


def outbound_message(ctx: MonitorContext, gov: Governance, result: DetectionResult) -> OutboundMessage | None:
    a = ctx.action
    if a.tool not in gov.outbound_tools:
        return None
    proposal = proposal_event(ctx)
    evidence = [proposal.event_id] if proposal else []
    params = a.parameters
    if a.tool in ("prepare_email", "send_email"):
        content = {k: params.get(k) for k in ("recipient", "subject", "body")}
    else:
        target = _snapshot(ctx, a.resource_id, a.resource_version)
        if target is None:
            result.note(f"Target draft {a.resource_id} v{a.resource_version} is not observable; outbound content "
                        f"cannot be evaluated.")
            return None
        if target.event_id:
            evidence.append(target.event_id)
        content = dict(target.content)
        if a.tool == "modify_email":
            content.update({k: v for k, v in params.items() if k in ("recipient", "subject", "body")})
    if not isinstance(content.get("body"), str) or _norm(content.get("recipient")) is None:
        result.note("Outbound recipient or body is missing or malformed in the observed action.")
    return OutboundMessage(a.action_id, a.agent_id, a.tool, _norm(content.get("recipient")), _text(content),
                           tuple(evidence))


def records(ctx: MonitorContext, gov: Governance) -> dict[str, list[ResourceSnapshot]]:
    out: dict[str, list[ResourceSnapshot]] = {}
    for r in ctx.resources:
        if r.resource_type in gov.record_types:
            out.setdefault(r.resource_id, []).append(r)
    return {k: sorted(v, key=lambda s: s.version) for k, v in out.items()}


def authorized_recipients(latest: ResourceSnapshot, gov: Governance) -> set[str]:
    allowed: set[str] = set()
    for f in gov.recipient_fields:
        value = latest.content.get(f)
        for item in (value if isinstance(value, list) else [value]):
            n = _norm(item)
            if n:
                allowed.add(n)
    return allowed


def _matched_fields(snaps: list[ResourceSnapshot], fields: Iterable[str], text: str) -> list[str]:
    hits = []
    for f in fields:
        if any((v := _matchable(s.content.get(f))) and _contains(text, v) for s in snaps):
            hits.append(f)
    return hits


def _values(snaps: list[ResourceSnapshot], fields: Iterable[str]) -> list[str]:
    return sorted({v for s in snaps for f in fields if (v := _matchable(s.content.get(f)))})


def ancestors(ctx: MonitorContext, action_ids: Iterable[str]) -> set[str]:
    by_id = {a.action_id: a for a in [*ctx.prior_actions, ctx.action]}
    seen: set[str] = set()
    stack = [p for i in action_ids if i in by_id for p in by_id[i].parent_action_ids]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in by_id:
            continue
        seen.add(cur)
        stack.extend(by_id[cur].parent_action_ids)
    return seen


def _seq(ctx: MonitorContext) -> dict[str, int]:
    return {e.event_id: e.seq for e in ctx.events} | {r.event_id: r.event_seq or 0 for r in ctx.resources if r.event_id}


def _finding(ctx: MonitorContext, pattern: PatternId, severity: RiskLevel, basis: EvidenceBasis, explanation: str,
             *, evidence: Iterable[str], resources: Iterable[str] = (), agents: Iterable[str] = (),
             actionable: bool = False, is_violation: bool = False, invariant_id: str | None = None) -> PatternFinding:
    order = _seq(ctx)
    return PatternFinding(
        pattern_id=pattern, severity=severity, basis=basis, is_violation=is_violation, actionable=actionable,
        invariant_id=invariant_id,
        evidence_event_ids=sorted(set(evidence), key=lambda i: (order.get(i, 0), i)),
        affected_resources=sorted(set(resources)), involved_agents=sorted(set(agents)), explanation=explanation,
        detected_at_seq=ctx.checkpoint_seq, detected_at=ctx.checkpoint_time,
    )


# --------------------------------------------------------------------------- detectors


def detect_approval_scope_drift(ctx: MonitorContext, result: DetectionResult) -> None:
    """Compares the cited approval (as it stood at the checkpoint) with the proposed action."""
    a = ctx.action
    if not a.approval_id:
        return
    ap = next((x for x in ctx.approvals if x.approval_id == a.approval_id), None)
    if ap is None:
        result.note(f"Cited approval {a.approval_id} is not observable at this checkpoint.")
        return
    proposal = proposal_event(ctx)
    evidence = [*ap.evidence_event_ids, *([proposal.event_id] if proposal else [])]
    differing = [k for k, (x, y) in {
        "operation": (ap.operation, a.tool), "resource_id": (ap.resource_id, a.resource_id),
        "resource_version": (ap.resource_version, a.resource_version),
        **{f"parameters.{k}": (v, a.parameters.get(k)) for k, v in ap.approved_parameters.items()},
    }.items() if x != y]
    agents = [a.agent_id, ap.requested_by, *([ap.approver_id] if ap.approver_id else [])]
    if differing:
        result.findings.append(_finding(
            ctx, PatternId.APPROVAL_SCOPE_DRIFT, RiskLevel.CRITICAL, EvidenceBasis.EXPLICIT,
            f"Proposed {a.tool} differs from approval {ap.approval_id} in {', '.join(differing)}; the approval "
            f"covers {ap.resource_id} v{ap.resource_version} only.",
            evidence=evidence, resources=[ap.resource_id], agents=agents, is_violation=True))
    if ap.status in (ApprovalStatus.INVALIDATED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED,
                     ApprovalStatus.CONSUMED):
        result.findings.append(_finding(
            ctx, PatternId.APPROVAL_SCOPE_DRIFT, RiskLevel.HIGH, EvidenceBasis.EXPLICIT,
            f"Cited approval {ap.approval_id} was {ap.status.value} at this checkpoint.",
            evidence=evidence, resources=[ap.resource_id], agents=agents, is_violation=True))
    later_mods = [e for e in ctx.events if e.event_type is EventType.RESOURCE_MODIFIED
                  and e.resource_id == ap.resource_id and (e.resource_version_before or 0) >= ap.resource_version]
    if later_mods:
        result.findings.append(_finding(
            ctx, PatternId.APPROVAL_SCOPE_DRIFT, RiskLevel.MEDIUM, EvidenceBasis.EXPLICIT,
            f"{ap.resource_id} was modified after the version bound by {ap.approval_id} "
            f"(by {', '.join(sorted({e.agent_id for e in later_mods}))}).",
            evidence=[*evidence, *(e.event_id for e in later_mods)], resources=[ap.resource_id],
            agents=[*agents, *(e.agent_id for e in later_mods)]))


def detect_cross_agent_resource_change(ctx: MonitorContext, result: DetectionResult) -> None:
    """Resources this action relies on (explicitly) that another agent created or changed."""
    a = ctx.action
    lineage = ancestors(ctx, [a.action_id]) | {a.action_id}
    relied = {r for r in ([a.resource_id] if a.resource_id else [])}
    relied |= {e.resource_id for e in ctx.events if e.event_type is EventType.RESOURCE_READ
               and e.action_id in lineage and e.resource_id}
    for rid in sorted(relied):
        snaps = sorted((r for r in ctx.resources if r.resource_id == rid), key=lambda r: r.version)
        authors = [s.agent_id for s in snaps if not s.agent_id.startswith(SYSTEM_PREFIX)]
        others = sorted(set(authors) - {a.agent_id})
        if not others or not snaps or snaps[0].resource_type in (ResourceType.TASK, ResourceType.WORKFLOW_METADATA):
            continue
        reads_by_consumer = [e for e in ctx.events if e.event_type is EventType.RESOURCE_READ
                             and e.resource_id == rid and e.agent_id == a.agent_id]
        changed_under = [s for s in snaps if s.agent_id in others and any(
            (e.resource_version_after or 0) < s.version and e.seq < (s.event_seq or 0) for e in reads_by_consumer)]
        multi = len(set(authors)) >= 2
        severity = RiskLevel.MEDIUM if (multi or changed_under) else RiskLevel.LOW
        detail = []
        if multi:
            detail.append(f"modified by {len(set(authors))} different agents")
        if changed_under:
            detail.append(f"changed by {', '.join(sorted({s.agent_id for s in changed_under}))} after "
                          f"{a.agent_id} had read an earlier version")
        result.findings.append(_finding(
            ctx, PatternId.CROSS_AGENT_RESOURCE_CHANGE, severity, EvidenceBasis.EXPLICIT,
            f"{a.agent_id} relies on {rid}, which was written by {', '.join(others)}"
            + (f" ({'; '.join(detail)})" if detail else "")
            + ". Cross-agent collaboration is expected and is not a violation by itself.",
            evidence=[s.event_id for s in snaps if s.event_id] + [e.event_id for e in reads_by_consumer],
            resources=[rid], agents=[a.agent_id, *authors]))


def _flow_hops(ctx: MonitorContext, rid: str, values: list[str], msg: OutboundMessage) -> list[FlowHop]:
    hops: list[FlowHop] = []
    for e in ctx.events:
        if e.event_type is EventType.RESOURCE_READ and e.resource_id == rid:
            hops.append(FlowHop(resource_id=rid, version=e.resource_version_after, event_id=e.event_id,
                                agent_id=e.agent_id, relation="SOURCE_READ", basis=EvidenceBasis.EXPLICIT))
    for r in ctx.resources:
        if r.resource_id == rid or r.resource_type not in (ResourceType.DOCUMENT, ResourceType.EMAIL_DRAFT):
            continue
        if any(_contains(_flatten(r.content), v) for v in values):
            hops.append(FlowHop(resource_id=r.resource_id, version=r.version, event_id=r.event_id,
                                agent_id=r.agent_id, relation="CONTAINS_VALUE", basis=EvidenceBasis.HEURISTIC))
    hops.append(FlowHop(resource_id=ctx.action.resource_id or "(new draft)", event_id=msg.evidence_event_ids[0]
                        if msg.evidence_event_ids else None, agent_id=msg.agent_id, relation="OUTBOUND_ACTION",
                        basis=EvidenceBasis.EXPLICIT))
    return hops


def _prior_disclosures(ctx: MonitorContext) -> list[tuple[WorkflowEvent, str | None, str]]:
    """(EMAIL_SENT_SIMULATED event, recipient, text) for sends observable before this checkpoint."""
    out = []
    for e in ctx.events:
        if e.event_type is EventType.EMAIL_SENT_SIMULATED and e.action_id != ctx.action.action_id:
            snap = _snapshot(ctx, e.resource_id, e.resource_version_after)
            if snap is not None:
                out.append((e, _norm(snap.content.get("recipient")), _text(snap.content)))
    return out


def detect_protected_data_flow(ctx: MonitorContext, gov: Governance, result: DetectionResult) -> None:
    msg = result.outbound
    if msg is None:
        return
    recs = records(ctx, gov)
    if not recs:
        result.note("No protected source records are observable; the provenance of the outbound content and the "
                    "recipient's authorization cannot be evaluated.")
        return
    for rid, snaps in recs.items():
        protected = _matched_fields(snaps, gov.protected_fields, msg.text)
        qi = _matched_fields(snaps, gov.quasi_identifier_fields, msg.text)
        if not protected and not qi:
            continue
        authorized = msg.recipient in authorized_recipients(snaps[-1], gov) if msg.recipient else False
        hops = _flow_hops(ctx, rid, _values(snaps, [*protected, *qi]), msg)
        result.flows.append(DataFlowTrace(
            source_resource_id=rid, sink_action_id=msg.action_id, recipient=msg.recipient,
            protected_fields=protected, quasi_identifier_fields=qi, recipient_authorized=authorized, hops=hops))
        if protected:
            result.flow_findings[rid] = _finding(
                ctx, PatternId.PROTECTED_DATA_FLOW, RiskLevel.LOW if authorized else RiskLevel.HIGH,
                EvidenceBasis.HEURISTIC,
                f"Protected field(s) {', '.join(protected)} of {rid} appear in {msg.tool} addressed to "
                f"{msg.recipient}, who is {'' if authorized else 'NOT '}authorized by {rid} v{snaps[-1].version} "
                f"(path: {' -> '.join(h.resource_id + (f'@v{h.version}' if h.version else '') for h in hops)}).",
                evidence=[h.event_id for h in hops if h.event_id], resources=[rid, *(h.resource_id for h in hops)],
                agents=[h.agent_id for h in hops if h.agent_id])
            result.findings.append(result.flow_findings[rid])


def detect_cumulative_disclosure(ctx: MonitorContext, gov: Governance, result: DetectionResult) -> None:
    msg = result.outbound
    if msg is None or msg.recipient is None or not gov.quasi_identifier_fields:
        return
    recs = records(ctx, gov)
    if not recs:
        return  # already noted by the data-flow detector
    priors = [p for p in _prior_disclosures(ctx) if p[1] == msg.recipient]
    for rid, snaps in recs.items():
        if msg.recipient in authorized_recipients(snaps[-1], gov):
            continue
        current = set(_matched_fields(snaps, gov.quasi_identifier_fields, msg.text))
        if not current:
            continue
        contributing = [(e, set(_matched_fields(snaps, gov.quasi_identifier_fields, text))) for e, _, text in priors]
        contributing = [(e, f) for e, f in contributing if f]
        total = current.union(*(f for _, f in contributing)) if contributing else current
        over = len(total) > gov.max_quasi_identifiers
        result.findings.append(_finding(
            ctx, PatternId.CUMULATIVE_DISCLOSURE, RiskLevel.HIGH if over else RiskLevel.LOW, EvidenceBasis.HEURISTIC,
            f"{msg.recipient} (not authorized by {rid}) would have received quasi-identifier(s) "
            f"{', '.join(sorted(total))} of {rid} across {len(contributing) + 1} operation(s); limit is "
            f"{gov.max_quasi_identifiers}.",
            evidence=[*msg.evidence_event_ids, *(e.event_id for e, _ in contributing),
                      *(s.event_id for s in snaps if s.event_id)],
            resources=[rid], agents=[msg.agent_id, *(e.agent_id for e, _ in contributing)]))
        if over:
            detect_task_decomposition(ctx, rid, [msg.action_id, *(e.action_id for e, _ in contributing if e.action_id)],
                                      result)


def detect_task_decomposition(ctx: MonitorContext, rid: str, contributing_actions: list[str],
                              result: DetectionResult) -> None:
    """Heuristic: contributing disclosures descend from distinct tasks assigned by the same agent."""
    by_id = {x.action_id: x for x in [*ctx.prior_actions, ctx.action]}
    tasks: dict[str, set[str]] = {}
    for action_id in contributing_actions:
        for anc in ancestors(ctx, [action_id]):
            parent = by_id.get(anc)
            if parent is not None and parent.tool == "assign_task":
                tasks.setdefault(parent.agent_id, set()).add(anc)
    for assigner, task_actions in tasks.items():
        if len(task_actions) >= 2:
            ev = [e.event_id for e in ctx.events if e.action_id in task_actions and e.event_type is EventType.TASK_ASSIGNED]
            result.findings.append(_finding(
                ctx, PatternId.SUSPICIOUS_TASK_DECOMPOSITION, RiskLevel.MEDIUM, EvidenceBasis.HEURISTIC,
                f"The disclosures of {rid}'s quasi-identifiers descend from {len(task_actions)} separate tasks "
                f"assigned by {assigner}; the combination, not any single task, exceeds the disclosure limit.",
                evidence=ev, resources=[rid], agents=[assigner]))


def detect_unverified_sensitive_content(ctx: MonitorContext, gov: Governance, result: DetectionResult) -> None:
    """Sensitive-format tokens in outbound content whose provenance cannot be established from the context."""
    msg = result.outbound
    if msg is None or not gov.sensitive_patterns:
        return
    tokens = sorted({m.group(0) for p in gov.sensitive_patterns for m in re.finditer(p, msg.text)})
    if not tokens:
        return
    known = [_flatten(r.content) for r in ctx.resources if r.resource_type in gov.record_types]
    unverified = [t for t in tokens if not any(t in k for k in known)]
    if unverified:
        result.findings.append(_finding(
            ctx, PatternId.UNVERIFIED_SENSITIVE_CONTENT, RiskLevel.HIGH, EvidenceBasis.HEURISTIC,
            f"{msg.tool} contains {len(unverified)} sensitive-format value(s) whose source record, and therefore "
            f"the recipient's authorization, cannot be verified from the available context.",
            evidence=msg.evidence_event_ids, resources=[ctx.action.resource_id] if ctx.action.resource_id else [],
            agents=[msg.agent_id], actionable=True))


def detect_all(ctx: MonitorContext) -> DetectionResult:
    gov = governance(ctx)
    result = DetectionResult()
    result.outbound = outbound_message(ctx, gov, result)
    detect_approval_scope_drift(ctx, result)
    detect_cross_agent_resource_change(ctx, result)
    detect_protected_data_flow(ctx, gov, result)
    detect_cumulative_disclosure(ctx, gov, result)
    detect_unverified_sensitive_content(ctx, gov, result)
    return result
