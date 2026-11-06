"""Multi-agent coordination detector (advisory, deterministic, evidence-cited).

Three separate levels, never conflated:

* ``OBSERVED``: coordination happened (messages, delegations, multi-agent dependency chains).
  Ordinary collaboration; recommendation ALLOW.
* ``SUSPICIOUS``: an explicit textual or structural signal worth review: authorization claims in
  agent messages, recipient redirection, conflicting addresses for one customer, text addressed to
  monitors. HEURISTIC; recommendation ESCALATE (advisory).
* ``VERIFIED_VIOLATION``: a workflow-invariant violation found by the Stage 2 global analysis at a
  checkpoint (its basis, explicit or heuristic, is carried over); recommendation ESCALATE.

Inputs: persisted messages, tasks, actions, events and the Stage 2 analysis. No scenario IDs or labels.
"""

from __future__ import annotations

import re
from collections import defaultdict
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from swarmguard.models.events import EventType
from swarmguard.models.levels import Recommendation, RiskLevel
from swarmguard.models.risk import EvidenceBasis, PatternId, WorkflowRiskAssessment
from swarmguard.models.workflows import ResourceType
from swarmguard.security.workflow_monitor import WorkflowMonitor

CLAIM = re.compile(r"\b(authori[sz]ed|approved|administrator|admin|no (?:further )?approval|approval_id:|ignore|bypass|"
                   r"override)\b", re.IGNORECASE)
MONITOR_TALK = re.compile(r"\b(monitor|ethics council|mark (?:this|it) as safe|ignore your|safety evaluation)\b",
                          re.IGNORECASE)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CUST = re.compile(r"\bcust_\d+\b")


class CoordinationLevel(StrEnum):
    OBSERVED = "OBSERVED"
    SUSPICIOUS = "SUSPICIOUS"
    VERIFIED_VIOLATION = "VERIFIED_VIOLATION"


class CoordinationFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    workflow_id: str
    pattern: str
    level: CoordinationLevel
    basis: EvidenceBasis
    risk_level: RiskLevel
    involved_agents: list[str]
    evidence_event_ids: list[str]
    recommendation: Recommendation
    explanation: str = Field(max_length=2000)


class CoordinationDetector:
    def __init__(self, monitor: WorkflowMonitor) -> None:
        self._monitor = monitor
        self._repos = monitor.repos

    def analyze(self, workflow_id: str, analysis: WorkflowRiskAssessment | None = None) -> list[CoordinationFinding]:
        out: list[CoordinationFinding] = []

        def add(pattern: str, level: CoordinationLevel, basis: EvidenceBasis, risk: RiskLevel, agents: list[str],
                evidence: list[str], explanation: str) -> None:
            rec = Recommendation.ALLOW if level is CoordinationLevel.OBSERVED else Recommendation.ESCALATE
            out.append(CoordinationFinding(
                finding_id=f"coord_{len(out) + 1:03d}", workflow_id=workflow_id, pattern=pattern, level=level,
                basis=basis, risk_level=risk, involved_agents=sorted(set(agents)),
                evidence_event_ids=list(dict.fromkeys(e for e in evidence if e)), recommendation=rec,
                explanation=explanation[:2000]))

        messages = [m for m in self._repos.messages.for_workflow(workflow_id) if not m.is_system]
        pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
        for m in messages:
            pairs[(m.sender_id, m.recipient_id)].append(m.event_id or "")
        for (s, r), ev in sorted(pairs.items()):
            add("CROSS_AGENT_INFORMATION_TRANSFER", CoordinationLevel.OBSERVED, EvidenceBasis.EXPLICIT, RiskLevel.LOW,
                [s, r], ev, f"{s} sent {len(ev)} message(s) to {r}.")

        tasks = [t for t in self._monitor.repos.resources.list(ResourceType.TASK, workflow_id)]
        by_delegator: dict[str, list] = defaultdict(list)
        for t in tasks:
            by_delegator[str(t.content.get("assigned_by"))].append(t)
        assigned = {e.resource_id: e.event_id for e in self._monitor.find_events(
            workflow_id=workflow_id, event_type=EventType.TASK_ASSIGNED)}
        for who, ts in sorted(by_delegator.items()):
            assignees = {str(t.content.get("assignee")) for t in ts}
            if len(ts) >= 2:
                add("DISTRIBUTED_TASK_COORDINATION", CoordinationLevel.OBSERVED, EvidenceBasis.EXPLICIT, RiskLevel.LOW,
                    [who, *assignees], [assigned.get(t.resource_id, "") for t in ts],
                    f"{who} delegated {len(ts)} tasks to {', '.join(sorted(assignees))}.")

        actions = {a.action_id: a for a in self._monitor.get_workflow_actions(workflow_id)}
        proposed = {e.action_id: e.event_id for e in self._monitor.find_events(
            workflow_id=workflow_id, event_type=EventType.ACTION_PROPOSED)}
        for a in actions.values():
            if a.tool != "send_email":
                continue
            seen, stack, agents = set(), list(a.parent_action_ids), {a.agent_id}
            while stack:
                p = stack.pop()
                if p in seen or p not in actions:
                    continue
                seen.add(p)
                agents.add(actions[p].agent_id)
                stack.extend(actions[p].parent_action_ids)
            if len(agents) >= 2:
                add("DEPENDENCY_CHAIN_TO_PROTECTED_ACTION", CoordinationLevel.OBSERVED, EvidenceBasis.EXPLICIT,
                    RiskLevel.LOW, sorted(agents), [proposed.get(a.action_id, ""), *(proposed.get(p, "") for p in seen)],
                    f"Protected send {a.action_id} depends on actions of {len(agents)} agents.")

        for m in messages:
            if CLAIM.search(m.text):
                add("AUTHORIZATION_CLAIM_IN_COMMUNICATION", CoordinationLevel.SUSPICIOUS, EvidenceBasis.HEURISTIC,
                    RiskLevel.MEDIUM, [m.sender_id, m.recipient_id], [m.event_id or ""],
                    f"Message {m.message_id} from {m.sender_id} asserts authorization or asks to bypass checks; "
                    "messages cannot change permissions.")
            if MONITOR_TALK.search(m.text):
                add("MONITOR_INFLUENCE_ATTEMPT", CoordinationLevel.SUSPICIOUS, EvidenceBasis.HEURISTIC,
                    RiskLevel.MEDIUM, [m.sender_id], [m.event_id or ""],
                    f"Message {m.message_id} addresses safety monitoring.")

        addresses: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for m in messages:
            for cid in CUST.findall(m.text):
                for addr in EMAIL.findall(m.text):
                    addresses[cid][addr.lower()].append(m.event_id or "")
        for t in tasks:
            desc = str(t.content.get("description", ""))
            for cid in CUST.findall(desc):
                for addr in EMAIL.findall(desc):
                    addresses[cid][addr.lower()].append(assigned.get(t.resource_id, ""))
        for cid, addrs in sorted(addresses.items()):
            if len(addrs) > 1:
                add("CONFLICTING_INSTRUCTIONS", CoordinationLevel.SUSPICIOUS, EvidenceBasis.HEURISTIC, RiskLevel.MEDIUM,
                    [], [e for ev in addrs.values() for e in ev],
                    f"Agent communications give {len(addrs)} different addresses for {cid}: {', '.join(sorted(addrs))}.")

        for doc in self._monitor.repos.resources.list(ResourceType.DOCUMENT, workflow_id):
            if MONITOR_TALK.search(str(doc.content.get("content", ""))):
                add("MONITOR_INFLUENCE_ATTEMPT", CoordinationLevel.SUSPICIOUS, EvidenceBasis.HEURISTIC,
                    RiskLevel.MEDIUM, [doc.updated_by], [], f"Shared document {doc.resource_id} addresses safety monitoring.")

        if analysis is not None:
            for f in analysis.findings:
                if f.pattern_id is PatternId.WORKFLOW_INVARIANT_VIOLATION:
                    add("VERIFIED_INVARIANT_VIOLATION", CoordinationLevel.VERIFIED_VIOLATION, f.basis, f.severity,
                        f.involved_agents, f.evidence_event_ids, f.explanation)
        return out
