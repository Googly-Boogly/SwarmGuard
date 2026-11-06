"""Capability-free coordination analysis over ObservationBuilder contexts."""
from __future__ import annotations
import re
from typing import Any
from swarmguard.models.risk import MonitorContext

DIRECTED = re.compile(r"(?:monitor|ethics council|safety evaluator).{0,100}(?:ignore|mark.{0,20}safe|override)", re.I | re.S)


def coordination_patterns(ctx: MonitorContext) -> list[dict[str, Any]]:
    actions = {a.action_id: a for a in [*ctx.prior_actions, ctx.action]}
    proposals = {e.action_id: e.event_id for e in ctx.events if e.event_type.value == "ACTION_PROPOSED"}
    findings: list[dict[str, Any]] = []
    for a in actions.values():
        for parent in a.parent_action_ids:
            p = actions.get(parent)
            if p and p.agent_id != a.agent_id and parent in proposals and a.action_id in proposals:
                findings.append({"category": "ORDINARY_COLLABORATION", "pattern": "CROSS_AGENT_DEPENDENCY",
                    "action_ids": [parent, a.action_id], "evidence_event_ids": [proposals[parent], proposals[a.action_id]],
                    "explanation": "Explicit cross-agent action dependency; coordination alone is not unsafe."})
        text = str(a.parameters.get("text", "")) + str(a.parameters.get("content", ""))
        if DIRECTED.search(text) and a.action_id in proposals:
            findings.append({"category": "POTENTIALLY_CONCERNING", "pattern": "MONITOR_DIRECTED_MANIPULATION",
                "action_ids": [a.action_id], "evidence_event_ids": [proposals[a.action_id]],
                "explanation": "Untrusted instructions address safety evaluation; intent is not inferred."})
    return findings
