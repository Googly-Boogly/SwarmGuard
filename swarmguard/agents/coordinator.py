"""Agent A: Coordinator. Receives objectives, assigns tasks, tracks dependencies."""

from __future__ import annotations

from swarmguard.agents.base import BaseAgent
from swarmguard.models.decisions import GatewayResponse
from swarmguard.security.identity import AgentRole


class CoordinatorAgent(BaseAgent):
    role = AgentRole.COORDINATOR

    async def assign_task(self, workflow_id: str, assignee: str, description: str, *,
                          depends_on_task_ids: list[str] | None = None,
                          parent_action_ids: list[str] | None = None) -> GatewayResponse:
        return await self.propose(
            workflow_id, "assign_task",
            {"assignee": assignee, "description": description, "depends_on_task_ids": depends_on_task_ids or []},
            parent_action_ids=parent_action_ids,
        )
