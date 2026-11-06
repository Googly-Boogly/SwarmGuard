"""Agent base class.

Agents are *untrusted*. Each holds exactly what the simulator gave it: a
read-only identity and an ``AgentGatewayClient`` bound to its credential. It
has no database, workspace, executor, policy, or approval handle, so its only
way to affect anything is to submit proposals to the capability gateway.

Stage 1 agents follow deterministic scripts. Stage 2 can replace the scripts
with LLM policies that emit the same ``ActionProposal`` objects.
"""

from __future__ import annotations

from typing import Any, ClassVar

from swarmguard.models.actions import ActionProposal
from swarmguard.models.decisions import GatewayResponse
from swarmguard.security.capability_gateway import AgentGatewayClient
from swarmguard.security.identity import AgentIdentity, AgentRole


class BaseAgent:
    role: ClassVar[AgentRole]

    def __init__(self, identity: AgentIdentity, client: AgentGatewayClient) -> None:
        if identity.role is not self.role:
            raise ValueError(f"{type(self).__name__} requires role {self.role}, got {identity.role}")
        self.__identity = identity
        self.__client = client
        self.responses: list[GatewayResponse] = []

    @property
    def identity(self) -> AgentIdentity:
        return self.__identity

    @property
    def agent_id(self) -> str:
        return self.__identity.agent_id

    async def propose(
        self,
        workflow_id: str,
        tool: str,
        parameters: dict[str, Any] | None = None,
        *,
        resource_id: str | None = None,
        resource_version: int | None = None,
        parent_action_ids: list[str] | None = None,
        approval_id: str | None = None,
        request_id: str | None = None,
    ) -> GatewayResponse:
        proposal = ActionProposal(
            workflow_id=workflow_id, tool=tool, parameters=parameters or {}, resource_id=resource_id,
            resource_version=resource_version, parent_action_ids=parent_action_ids or [],
            approval_id=approval_id, request_id=request_id,
        )
        response = await self.__client.submit(proposal)
        self.responses.append(response)
        return response

    async def submit_raw(self, proposal: Any) -> GatewayResponse:
        """Submit an arbitrary (possibly malformed or malicious) proposal. Used by tests."""
        response = await self.__client.submit(proposal)
        self.responses.append(response)
        return response

    async def read(self, workflow_id: str, tool: str, resource_id: str, *,
                   parent_action_ids: list[str] | None = None) -> GatewayResponse:
        return await self.propose(workflow_id, tool, resource_id=resource_id, parent_action_ids=parent_action_ids)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.agent_id})"
