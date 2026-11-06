"""LangGraph StateGraph plus LangChain StructuredTool, public APIs only."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult


class PipelineState(TypedDict):
    completed: int


class LangGraphAdapter(FrameworkAdapter):
    framework = 'langgraph'

    def check_dependencies(self) -> None:
        from langgraph.graph import StateGraph
        from langchain_core.tools import StructuredTool
        if not callable(StateGraph) or not callable(StructuredTool.from_function):
            raise RuntimeError('required public LangGraph interfaces unavailable')

    def make_tool(self, agent: object) -> Any:
        from langchain_core.tools import StructuredTool
        client = self.runtime.client(agent)

        async def gateway_tool(invocation: ToolInvocation) -> str:
            return (await client.execute_protected_tool(invocation)).model_dump_json()

        return StructuredTool.from_function(coroutine=gateway_tool, name='swarmguard_gateway',
                                           description='Propose a simulated operation to the bound SwarmGuard gateway.')

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        raw = await self.make_tool(agent).ainvoke({'invocation': invocation.model_dump()})
        return ToolResult.model_validate_json(raw)

    async def run_pipeline(self, stages: list[Callable[[], Awaitable[None]]]) -> None:
        from langgraph.graph import StateGraph, START, END
        graph = StateGraph(PipelineState)
        previous = START
        for index, stage in enumerate(stages):
            async def node(state: PipelineState, operation: Callable[[], Awaitable[None]] = stage) -> dict[str, int]:
                await operation()
                return {'completed': state['completed'] + 1}
            name = f'stage_{index}'
            graph.add_node(name, node)
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        await graph.compile().ainvoke({'completed': 0}, config={'recursion_limit': 20})
