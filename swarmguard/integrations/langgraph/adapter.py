"""LangGraph StateGraph, prebuilt ToolNode and LangChain StructuredTool; public APIs only.

Enforcement boundary: the gateway-bound ``StructuredTool`` is the only tool given to
the ``ToolNode``. Tool calls are carried as ``AIMessage.tool_calls`` exactly as a
model-driven graph would carry them; LangGraph's ToolNode resolves and dispatches
them. Unregistered tool names are refused by ToolNode and never reach an executor.
Streamed node updates and checkpoint IDs are recorded as untrusted observations.
"""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult

GATEWAY_TOOL = 'swarmguard_gateway'


class PipelineState(TypedDict):
    completed: int


class LangGraphAdapter(FrameworkAdapter):
    framework = 'langgraph'

    def __init__(self, runtime) -> None:
        super().__init__(runtime)
        self._tool_graphs: dict[str, Any] = {}
        self.checkpoint_refs: list[str] = []

    def check_dependencies(self) -> None:
        from langgraph.graph import StateGraph
        from langgraph.prebuilt import ToolNode
        from langgraph.checkpoint.memory import InMemorySaver
        from langchain_core.tools import StructuredTool
        from langchain_core.messages import AIMessage
        if not all(callable(x) for x in (StateGraph, ToolNode, InMemorySaver, AIMessage, StructuredTool.from_function)):
            raise RuntimeError('required public LangGraph interfaces unavailable')

    def make_tool(self, agent: object) -> Any:
        from langchain_core.tools import StructuredTool
        client = self.runtime.client(agent)

        async def gateway_tool(invocation: ToolInvocation) -> str:
            return (await client.execute_protected_tool(invocation)).model_dump_json()

        return StructuredTool.from_function(coroutine=gateway_tool, name=GATEWAY_TOOL,
                                           description='Propose a simulated operation to the bound SwarmGuard gateway.')

    def tool_graph(self, agent: object) -> Any:
        """Compiled per-agent graph: proposal message -> ToolNode with only the gateway tool."""
        from langchain_core.messages import AnyMessage
        from langgraph.graph import StateGraph, START, END
        from langgraph.graph.message import add_messages
        from langgraph.prebuilt import ToolNode
        aid = self.runtime.mapping.resolve(agent)
        if aid not in self._tool_graphs:
            # Functional form: annotations are evaluated now (module uses postponed annotations).
            ToolState = TypedDict('ToolState', {'messages': Annotated[list[AnyMessage], add_messages]})
            graph = StateGraph(ToolState)
            graph.add_node('tools', ToolNode([self.make_tool(agent)]))
            graph.add_edge(START, 'tools')
            graph.add_edge('tools', END)
            # checkpointer=False: never inherit/accumulate the outer workflow checkpoint state.
            self._tool_graphs[aid] = graph.compile(checkpointer=False, name=f'{aid}_tools')
        return self._tool_graphs[aid]

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        from langchain_core.messages import AIMessage, ToolMessage
        message = AIMessage(content='', tool_calls=[{'name': GATEWAY_TOOL, 'id': invocation.tool_call_id,
                                                     'args': {'invocation': invocation.model_dump()}}])
        out = await self.tool_graph(agent).ainvoke({'messages': [message]}, config={'recursion_limit': 5})
        replies = [m for m in out['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == invocation.tool_call_id]
        if len(replies) != 1 or getattr(replies[0], 'status', 'success') == 'error':
            raise RuntimeError('LangGraph ToolNode did not return a gateway decision; no execution assumed')
        return ToolResult.model_validate_json(replies[0].content)

    async def run_pipeline(self, stages: list[tuple[object, Callable[[], Awaitable[None]]]]) -> None:
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.memory import InMemorySaver
        graph = StateGraph(PipelineState)
        previous = START
        owners: dict[str, object] = {}
        for index, (owner, stage) in enumerate(stages):
            async def node(state: PipelineState, operation: Callable[[], Awaitable[None]] = stage) -> dict[str, int]:
                await operation()
                return {'completed': state['completed'] + 1}
            name = f'stage_{index}'
            owners[name] = owner
            graph.add_node(name, node)
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        config = {'configurable': {'thread_id': self.runtime.workflow_id}, 'recursion_limit': 20}
        app = graph.compile(checkpointer=InMemorySaver())
        async for update in app.astream({'completed': 0}, config, stream_mode='updates'):
            for node_name, values in update.items():
                owner = owners.get(node_name)
                if owner is None:
                    continue
                snapshot = await app.aget_state(config)
                checkpoint = snapshot.config.get('configurable', {}).get('checkpoint_id')
                if checkpoint:
                    self.checkpoint_refs.append(str(checkpoint))
                self.runtime.event(self.runtime.mapping.resolve(owner), 'AGENT_ACTIVITY', source='FRAMEWORK_REPORTED',
                    metadata={'native_event': 'StateGraph.updates', 'surface': 'langgraph_stream',
                              'node': node_name, 'state_update': values,
                              'checkpoint_id': str(checkpoint) if checkpoint else None})

    async def close(self) -> None:
        if self._initialized:
            self.update_record(checkpoint_refs=self.checkpoint_refs[-20:])
        await super().close()
