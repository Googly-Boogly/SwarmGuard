"""CrewAI public BaseTool extension and Flow orchestration; no private patches."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import Any
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult


class CrewAIAdapter(FrameworkAdapter):
    framework = 'crewai'

    def check_dependencies(self) -> None:
        from crewai.tools import BaseTool
        from crewai.flow.flow import Flow, start, listen
        if not all(callable(x) for x in (BaseTool, Flow, start, listen)):
            raise RuntimeError('required public CrewAI tool/flow APIs unavailable')

    def make_tool(self, agent: object) -> Any:
        from crewai.tools import BaseTool
        from pydantic import BaseModel
        client = self.runtime.client(agent)

        class GatewayArguments(BaseModel):
            invocation: ToolInvocation

        class GatewayTool(BaseTool):
            name: str = 'swarmguard_gateway'
            description: str = 'Credential-bound simulated operation; mandatory gateway approval and version checks.'
            args_schema: type[BaseModel] = GatewayArguments
            cache_function: Any = lambda *_: False

            def _run(self, invocation: Any) -> str:
                # CrewAI documents this extension point. Sync callers get the same
                # gateway, never a direct executor. Async applications use arun.
                import asyncio
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return asyncio.run(self._arun(invocation))
                raise RuntimeError('use await tool.arun(...) inside an async application')

            async def _arun(self, invocation: Any) -> str:
                parsed = invocation if isinstance(invocation, ToolInvocation) else ToolInvocation.model_validate(invocation)
                return (await client.execute_protected_tool(parsed)).model_dump_json()

        return GatewayTool()

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        raw = await self.make_tool(agent).arun(invocation=invocation.model_dump())
        return ToolResult.model_validate_json(raw)

    async def run_pipeline(self, stages: list[Callable[[], Awaitable[None]]]) -> None:
        from crewai.flow.flow import Flow, start, listen
        methods: dict[str, Any] = {}
        previous = None
        for index, operation in enumerate(stages):
            async def step(self, *args: Any, operation: Callable[[], Awaitable[None]] = operation) -> int:
                await operation()
                return 1
            name = f'stage_{index}'
            step.__name__ = name
            methods[name] = start()(step) if previous is None else listen(previous)(step)
            previous = name
        flow = type('SwarmGuardSyntheticFlow', (Flow,), methods)(tracing=False,suppress_flow_events=True)
        from swarmguard.integrations.event_normalizer import FrameworkEvent
        if self._agents:
            native_id = flow.state.get('id') if isinstance(flow.state,dict) else flow.state.id
            await self.normalize_event(next(iter(self._agents.values())), FrameworkEvent(event_type='AGENT_ACTIVITY',
                metadata={'native_flow_id':str(native_id) if native_id is not None else None,'surface':'CrewAI Flow'}))
        await flow.kickoff_async()
