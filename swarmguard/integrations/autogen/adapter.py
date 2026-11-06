"""Microsoft AutoGen Core 0.7: SingleThreadedAgentRuntime, ToolAgent, FunctionTool.

Enforcement boundary: each host-registered ToolAgent holds only the gateway-bound
``FunctionTool``. Tool calls travel as ``FunctionCall`` messages through the AutoGen
runtime; ToolAgent dispatches them and rejects unknown tool names. A public
``DefaultInterventionHandler`` records message traffic as untrusted observations; it
never drops, rewrites, approves or executes anything.
"""
from __future__ import annotations
import json
from typing import Any
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult

GATEWAY_TOOL = 'swarmguard_gateway'


class AutoGenAdapter(FrameworkAdapter):
    framework = 'autogen'

    def __init__(self, runtime) -> None:
        super().__init__(runtime)
        self._framework_runtime = None
        self._addresses: dict[str, Any] = {}
        self.unattributed_native_events = 0

    def check_dependencies(self) -> None:
        from autogen_core import SingleThreadedAgentRuntime, DefaultInterventionHandler
        from autogen_core.tool_agent import ToolAgent
        from autogen_core.tools import FunctionTool
        if not all(callable(x) for x in (SingleThreadedAgentRuntime, DefaultInterventionHandler, ToolAgent, FunctionTool)):
            raise RuntimeError('required public AutoGen Core APIs unavailable')

    def make_tool(self, agent: object) -> Any:
        from autogen_core.tools import FunctionTool
        client = self.runtime.client(agent)

        async def gateway_tool(invocation: ToolInvocation) -> str:
            return (await client.execute_protected_tool(invocation)).model_dump_json()

        return FunctionTool(gateway_tool, description='Bound simulated SwarmGuard capability; no arbitrary executor.',
                            name=GATEWAY_TOOL)

    def _observer(self) -> Any:
        from autogen_core import DefaultInterventionHandler, FunctionCall
        from autogen_core.models import FunctionExecutionResult
        adapter = self

        class Observer(DefaultInterventionHandler):
            def _record(self, address: Any, kind: str, native: str, call_id: Any, name: Any) -> None:
                aid = getattr(address, 'type', None)
                if aid not in adapter._addresses:   # only host-registered agent addresses
                    adapter.unattributed_native_events += 1
                    return
                adapter.runtime.event(aid, kind, source='FRAMEWORK_REPORTED',
                    tool_call_id=call_id if isinstance(call_id, str) and 1 <= len(call_id) <= 128 else None,
                    tool=name if isinstance(name, str) else None,
                    metadata={'native_event': native, 'surface': 'autogen_core.InterventionHandler'})

            async def on_send(self, message: Any, *, message_context: Any, recipient: Any) -> Any:
                if isinstance(message, FunctionCall):
                    self._record(recipient, 'MESSAGE', 'on_send.FunctionCall', message.id, message.name)
                return message

            async def on_response(self, message: Any, *, sender: Any, recipient: Any) -> Any:
                if isinstance(message, FunctionExecutionResult):
                    self._record(sender, 'TOOL_FAILED' if message.is_error else 'TOOL_EXECUTED',
                                 'on_response.FunctionExecutionResult', message.call_id, message.name)
                return message

        return Observer()

    async def register_agent(self, agent: object, trusted_agent_id: str):
        from autogen_core import SingleThreadedAgentRuntime, AgentId
        from autogen_core.tool_agent import ToolAgent
        client = await super().register_agent(agent, trusted_agent_id)
        if self._framework_runtime is None:
            self._framework_runtime = SingleThreadedAgentRuntime(intervention_handlers=[self._observer()])
            self._framework_runtime.start()
        tool = self.make_tool(agent)
        await ToolAgent.register(self._framework_runtime, trusted_agent_id,
            lambda: ToolAgent(description='Host-bound synthetic tool participant', tools=[tool]))
        self._addresses[trusted_agent_id] = AgentId(trusted_agent_id, self.runtime.workflow_id)
        return client

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        from autogen_core import FunctionCall
        from autogen_core.tool_agent import ToolException
        aid = self.runtime.mapping.resolve(agent)
        try:
            result = await self._framework_runtime.send_message(
                FunctionCall(id=invocation.tool_call_id, name=GATEWAY_TOOL,
                             arguments=json.dumps({'invocation': invocation.model_dump()})), self._addresses[aid])
        except ToolException as exc:  # BaseException subclass in AutoGen 0.7: never let it escape fail-closed handlers
            raise RuntimeError(f'AutoGen ToolAgent error ({type(exc).__name__}); no execution assumed') from None
        if getattr(result, 'is_error', False):
            raise RuntimeError('AutoGen ToolAgent returned an error; no execution assumed')
        return ToolResult.model_validate_json(result.content)

    async def close(self) -> None:
        if self._framework_runtime is not None:
            await self._framework_runtime.stop_when_idle()
            await self._framework_runtime.close()
            self._framework_runtime = None
        if self._initialized:
            self.update_record(unattributed_native_events=self.unattributed_native_events)
        await super().close()
