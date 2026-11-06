"""Reference integration: host-owned objects and bound async Python tools."""
from __future__ import annotations
from typing import Any
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult


class CustomAdapter(FrameworkAdapter):
    framework = 'custom'

    def check_dependencies(self) -> None:
        pass  # Only the existing SwarmGuard runtime is required.

    def make_tool(self, agent: object) -> Any:
        client = self.runtime.client(agent)
        return client.execute_protected_tool

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        return await self.make_tool(agent)(invocation)
