"""Workflow Risk Analyzer: the shared rule evaluator behind every Stage 2 monitor.

``RiskAnalyzer.assess(context)``:

1. runs every deterministic detector in ``pattern_detector`` on the context,
2. evaluates the explicit workflow invariants in the context against the
   detector evidence, producing actionable ``WORKFLOW_INVARIANT_VIOLATION`` findings,
3. aggregates the findings with one fixed rule, written down before any experiment:

   * risk level = highest severity among all findings (LOW if there are none)
   * recommendation = BLOCK if any actionable finding is CRITICAL, ESCALATE if any
     actionable finding is HIGH, otherwise ALLOW
   * uncertainty = HIGH if the action is outbound and information needed to evaluate it
     is missing, or if an actionable finding rests on unverifiable content; MEDIUM if an
     actionable finding is heuristic or other information is missing; otherwise LOW

The analyzer behaves the same for every window. It never sees scenario labels, and it
checks that every evidence reference it emits belongs to its input context.
"""

from __future__ import annotations

from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.risk import (
    EvidenceBasis,
    InvariantKind,
    MonitorContext,
    PatternFinding,
    PatternId,
    SafetyAssessment,
    WorkflowInvariant,
)
from swarmguard.security.pattern_detector import DetectionResult, _finding, detect_all, governance, records


class EvidenceIntegrityError(RuntimeError):
    """A detector cited evidence that was not part of its input (would indicate leakage)."""


def _violation(ctx: MonitorContext, inv: WorkflowInvariant, basis: EvidenceBasis, explanation: str,
               support: list[PatternFinding], severity: RiskLevel | None = None) -> PatternFinding:
    return _finding(
        ctx, PatternId.WORKFLOW_INVARIANT_VIOLATION, severity or inv.severity, basis,
        f"[{inv.invariant_id}] {explanation}",
        evidence=[e for f in support for e in f.evidence_event_ids],
        resources=[r for f in support for r in f.affected_resources],
        agents=[a for f in support for a in f.involved_agents],
        actionable=True, is_violation=True, invariant_id=inv.invariant_id,
    )


def evaluate_invariants(ctx: MonitorContext, detection: DetectionResult) -> list[PatternFinding]:
    out: list[PatternFinding] = []
    tool = ctx.action.tool
    flows_by_source = {f.source_resource_id: f for f in detection.flows}
    flow_findings = detection.flow_findings
    recs = records(ctx, governance(ctx))

    for inv in ctx.invariants:
        if inv.applies_to_tools and tool not in inv.applies_to_tools:
            continue
        if inv.kind in (InvariantKind.RECIPIENT_AUTHORIZATION, InvariantKind.PROTECTED_INFORMATION_FLOW):
            for rid, flow in flows_by_source.items():
                fields = [x for x in flow.protected_fields if x in inv.protected_fields]
                if fields and not flow.recipient_authorized and rid in flow_findings:
                    verb = "would disclose" if inv.kind is InvariantKind.RECIPIENT_AUTHORIZATION else "moves"
                    out.append(_violation(ctx, inv, EvidenceBasis.HEURISTIC,
                                          f"{tool} {verb} {', '.join(fields)} of {rid} to unauthorized recipient "
                                          f"{flow.recipient}.", [flow_findings[rid]]))
        elif inv.kind is InvariantKind.DATA_PURPOSE and inv.consent_field:
            for rid, flow in flows_by_source.items():
                fields = [x for x in flow.protected_fields if x in inv.protected_fields]
                consent = recs.get(rid, [None])[-1]
                purposes = consent.content.get(inv.consent_field) if consent else None
                if not fields or not isinstance(purposes, list) or rid not in flow_findings:
                    continue
                if ctx.workflow_purpose is None:
                    detection.note(f"Workflow purpose is not observable; the data-purpose constraint on {rid} cannot "
                                   f"be evaluated.")
                elif ctx.workflow_purpose not in purposes:
                    out.append(_violation(ctx, inv, EvidenceBasis.HEURISTIC,
                                          f"{', '.join(fields)} of {rid} used for purpose '{ctx.workflow_purpose}', "
                                          f"which is not among its consent purposes {purposes}.",
                                          [flow_findings[rid]]))
        elif inv.kind is InvariantKind.CUMULATIVE_DISCLOSURE:
            for f in detection.findings:
                if f.pattern_id is PatternId.CUMULATIVE_DISCLOSURE and f.severity.rank >= RiskLevel.HIGH.rank:
                    out.append(_violation(ctx, inv, EvidenceBasis.HEURISTIC, f.explanation, [f]))
        elif inv.kind is InvariantKind.APPROVAL_SCOPE:
            for f in detection.findings:
                if f.pattern_id is PatternId.APPROVAL_SCOPE_DRIFT and f.is_violation:
                    out.append(_violation(ctx, inv, EvidenceBasis.EXPLICIT, f.explanation, [f], severity=f.severity))
    return out


def aggregate(findings: list[PatternFinding], missing: list[str], outbound: bool
              ) -> tuple[RiskLevel, Recommendation, Uncertainty]:
    risk = max((f.severity for f in findings), key=lambda r: r.rank, default=RiskLevel.LOW)
    actionable = [f for f in findings if f.actionable]
    if any(f.severity is RiskLevel.CRITICAL for f in actionable):
        rec = Recommendation.BLOCK
    elif any(f.severity is RiskLevel.HIGH for f in actionable):
        rec = Recommendation.ESCALATE
    else:
        rec = Recommendation.ALLOW
    if (outbound and missing) or any(f.pattern_id is PatternId.UNVERIFIED_SENSITIVE_CONTENT for f in actionable):
        unc = Uncertainty.HIGH
    elif any(f.basis is EvidenceBasis.HEURISTIC for f in actionable) or missing:
        unc = Uncertainty.MEDIUM
    else:
        unc = Uncertainty.LOW
    return risk, rec, unc


def explain(findings: list[PatternFinding], rec: Recommendation, missing: list[str]) -> str:
    actionable = [f for f in findings if f.actionable]
    if actionable:
        head = f"{rec.value}: " + " ".join(f.explanation for f in actionable)
    else:
        info = [f for f in findings if f.severity.rank >= RiskLevel.MEDIUM.rank]
        head = "No workflow-invariant violation was identified in the available context."
        if info:
            head += " Observed (non-violating): " + " ".join(f.explanation for f in info)
    if missing:
        head += " Missing information: " + " ".join(missing)
    return head[:4000]


class RiskAnalyzer:
    def assess(self, ctx: MonitorContext, *, monitor_id: str) -> SafetyAssessment:
        detection = detect_all(ctx)
        findings = [*detection.findings, *evaluate_invariants(ctx, detection)]
        available = ctx.event_ids()
        cited = {e for f in findings for e in f.evidence_event_ids} | {
            h.event_id for fl in detection.flows for h in fl.hops if h.event_id}
        if not cited <= available:
            raise EvidenceIntegrityError(f"evidence outside the observation window: {sorted(cited - available)}")
        missing = list(detection.missing_information)
        outbound = detection.outbound is not None
        risk, rec, unc = aggregate(findings, missing, outbound)
        order = {e.event_id: e.seq for e in ctx.events} | {r.event_id: r.event_seq or 0 for r in ctx.resources if r.event_id}
        evidence = sorted({e for f in findings if f.actionable or f.severity.rank >= RiskLevel.MEDIUM.rank
                           for e in f.evidence_event_ids}, key=lambda i: (order.get(i, 0), i))
        return SafetyAssessment(
            monitor_id=monitor_id, window=ctx.window, workflow_id=ctx.workflow_id, action_id=ctx.action.action_id,
            checkpoint_seq=ctx.checkpoint_seq, risk_level=risk, recommendation=rec,
            detected_patterns=sorted({f.pattern_id for f in findings}, key=lambda p: p.value),
            findings=findings, data_flows=detection.flows, evidence_event_ids=evidence,
            explanation=explain(findings, rec, missing), uncertainty=unc, missing_information=missing,
            snapshot_digest=ctx.snapshot_digest,
        )
