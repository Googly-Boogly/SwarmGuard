"""Agent-facing bound capability. Registration and approval are absent from this API."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from swarmguard.models.decisions import GatewayResponse


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    tool: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    parameters: dict[str, Any] = Field(default_factory=dict)
    resource_id: str | None = None
    resource_version: int | None = Field(default=None, ge=1)
    approval_id: str | None = None
    parent_action_ids: list[str] = Field(default_factory=list, max_length=32)
    tool_call_id: str = Field(min_length=1, max_length=128)


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    gateway: GatewayResponse
    advisories: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str | None = None


class SwarmGuardClient:
    """Created by a trusted adapter, bound to one workflow and credential.

    This is an in-process client, not a remote transport or an authority factory.
    """
    __slots__ = ('__submit',)

    def __init__(self, submit: Callable[[dict[str, Any]], Awaitable[ToolResult]]) -> None:
        self.__submit = submit

    async def execute_protected_tool(self, invocation: ToolInvocation) -> ToolResult:
        return await self.__submit(invocation.model_dump())

    async def submit_proposal(self, proposal: dict[str, Any]) -> ToolResult:
        """Malformed/forged proposals still reach the gateway's persisted rejection path."""
        return await self.__submit(proposal)
