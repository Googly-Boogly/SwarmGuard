"""Stage 5 observation integrity checks; no mutable state access."""
from __future__ import annotations
from swarmguard.models.risk import MonitorContext
from swarmguard.security.risk_analyzer import EvidenceIntegrityError


def validate_context(ctx: MonitorContext) -> None:
    if ctx.action.workflow_id != ctx.workflow_id:
        raise EvidenceIntegrityError("action is outside the workflow")
    if any(e.workflow_id != ctx.workflow_id or e.seq > ctx.checkpoint_seq for e in ctx.events):
        raise EvidenceIntegrityError("future or foreign event in observation")
    if any((r.event_seq or 0) > ctx.checkpoint_seq for r in ctx.resources):
        raise EvidenceIntegrityError("future resource version in observation")
    if any(a.workflow_id != ctx.workflow_id or (ctx.checkpoint_time and a.created_at > ctx.checkpoint_time)
           for a in ctx.prior_actions):
        raise EvidenceIntegrityError("future or foreign prior action in observation")


def validate_evidence(ctx: MonitorContext, references: list[str]) -> None:
    validate_context(ctx)
    if not set(references) <= ctx.event_ids():
        raise EvidenceIntegrityError("assessment cites evidence outside its observation")
