"""Microsoft AutoGen Core 0.7 ToolAgent runtime and FunctionTool wrappers."""
from __future__ import annotations
import json
from typing import Any
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult


class AutoGenAdapter(FrameworkAdapter):
    framework = 'autogen'

    def __init__(self, runtime) -> None:
        super().__init__(runtime)
        self._framework_runtime = None
        self._addresses: dict[str, Any] = {}

    def check_dependencies(self) -> None:
        from autogen_core import SingleThreadedAgentRuntime
        from autogen_core.tool_agent import ToolAgent
        from autogen_core.tools import FunctionTool
        if not all(callable(x) for x in (SingleThreadedAgentRuntime, ToolAgent, FunctionTool)):
            raise RuntimeError('required public AutoGen Core APIs unavailable')

    def make_tool(self, agent: object) -> Any:
        from autogen_core.tools import FunctionTool
        client = self.runtime.client(agent)

        async def gateway_tool(invocation: ToolInvocation) -> str:
            return (await client.execute_protected_tool(invocation)).model_dump_json()

        return FunctionTool(gateway_tool, description='Bound simulated SwarmGuard capability; no arbitrary executor.',
                            name='swarmguard_gateway')

    async def register_agent(self, agent: object, trusted_agent_id: str):
        from autogen_core import SingleThreadedAgentRuntime, AgentId
        from autogen_core.tool_agent import ToolAgent
        client = await super().register_agent(agent, trusted_agent_id)
        if self._framework_runtime is None:
            self._framework_runtime = SingleThreadedAgentRuntime()
            self._framework_runtime.start()
        tool = self.make_tool(agent)
        await ToolAgent.register(self._framework_runtime, trusted_agent_id,
            lambda: ToolAgent(description='Host-bound synthetic tool participant', tools=[tool]))
        self._addresses[trusted_agent_id] = AgentId(trusted_agent_id, self.runtime.workflow_id)
        return client

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        from autogen_core import FunctionCall
        aid = self.runtime.mapping.resolve(agent)
        result = await self._framework_runtime.send_message(
            FunctionCall(id=invocation.tool_call_id, name='swarmguard_gateway',
                         arguments=json.dumps({'invocation': invocation.model_dump()})), self._addresses[aid])
        return ToolResult.model_validate_json(result.content)

    async def close(self) -> None:
        if self._framework_runtime is not None:
            await self._framework_runtime.stop_when_idle()
            await self._framework_runtime.close()
        await super().close()
