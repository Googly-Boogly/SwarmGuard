"""Read-only views of registered agents and their runtime status (for the API)."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from swarmguard.storage.repositories import Repositories


class AgentStatus(BaseModel):
    agent_id: str
    role: str
    display_name: str
    is_adversarial: bool
    workflows: list[str]
    decisions: int
    decisions_by_type: dict[str, int]
    rejected_decisions: int
    last_decision: str | None
    last_workflow: str | None


def list_agents(repos: Repositories) -> list[dict]:
    return repos.agent_registry.list()


def agent_status(repos: Repositories, agent_id: str) -> AgentStatus | None:
    entry = next((a for a in repos.agent_registry.list() if a["agent_id"] == agent_id), None)
    if entry is None:
        return None
    rows = repos.agent_registry.decisions_by_agent(agent_id)
    workflows = sorted({w.workflow_id for w in repos.workflows.list() if agent_id in w.participants})
    return AgentStatus(
        **entry, workflows=workflows, decisions=len(rows),
        decisions_by_type=dict(sorted(Counter(r.get("decision_type") or "INVALID" for r in rows).items())),
        rejected_decisions=sum(1 for r in rows if not r.get("valid")),
        last_decision=rows[-1].get("decision_type") if rows else None,
        last_workflow=rows[-1].get("workflow_id") if rows else None)
