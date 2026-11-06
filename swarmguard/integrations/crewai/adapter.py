"""CrewAI public APIs only: BaseTool, BaseLLM, Agent/Task/Crew, Flow and the event bus.

Enforcement boundary: the only protected capability handed to a CrewAI agent is a
gateway-bound ``BaseTool``. CrewAI's own ReAct parser, tool selection, argument
validation and tool dispatch run for real; the tool forwards the parsed proposal to
the trusted gateway on the owner event loop. Event-bus records are untrusted
observations and never become authorization identity or execution proof.
"""
from __future__ import annotations
import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from swarmguard.integrations.base import FrameworkAdapter
from swarmguard.sdk.client import ToolInvocation, ToolResult

GATEWAY_TOOL = 'swarmguard_gateway'
# Native CrewAI event class -> canonical kind. All are FRAMEWORK_REPORTED.
NATIVE_EVENTS = {
    'TaskStartedEvent': 'TASK_ASSIGNED',
    'AgentExecutionStartedEvent': 'AGENT_ACTIVITY',
    'ToolUsageStartedEvent': 'TOOL_CALL_PROPOSED',
    'ToolUsageFinishedEvent': 'TOOL_EXECUTED',
    'ToolUsageErrorEvent': 'TOOL_FAILED',
    'AgentExecutionErrorEvent': 'WORKFLOW_ERROR',
    'TaskFailedEvent': 'WORKFLOW_ERROR',
}
TOOL_DISPATCH_TIMEOUT_SECONDS = 60


def scripted_llm() -> Any:
    """Offline deterministic CrewAI ``BaseLLM`` used by synthetic fixtures.

    It stands in for the model: it emits ReAct text that CrewAI itself parses. It is
    a labelled synthetic script, never a live model and never a source of authority.
    """
    from crewai import BaseLLM

    class ScriptedReActLLM(BaseLLM):
        def __init__(self) -> None:
            super().__init__(model='synthetic-script-v1', temperature=0)
            self._queue: list[str] = []
            self._lock = threading.Lock()

        def enqueue(self, *responses: str) -> None:
            with self._lock:
                self._queue.extend(responses)

        def call(self, messages: Any, *args: Any, **kwargs: Any) -> str:
            with self._lock:
                if self._queue:
                    return self._queue.pop(0)
            return 'Thought: I now know the final answer\nFinal Answer: No further synthetic operation.'

        def supports_function_calling(self) -> bool:
            return False

        def get_context_window_size(self) -> int:
            return 8192

    return ScriptedReActLLM()


class CrewAIAdapter(FrameworkAdapter):
    framework = 'crewai'

    def __init__(self, runtime) -> None:
        super().__init__(runtime)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._results: dict[str, ToolResult] = {}
        self._native: list[tuple[str, Any]] = []
        self._native_lock = threading.Lock()
        self.unattributed_native_events = 0

    def check_dependencies(self) -> None:
        from crewai import Agent, Crew, Task, BaseLLM
        from crewai.tools import BaseTool
        from crewai.flow.flow import Flow, start, listen
        from crewai.events import crewai_event_bus
        if not all(callable(x) for x in (Agent, Crew, Task, BaseLLM, BaseTool, Flow, start, listen)):
            raise RuntimeError('required public CrewAI agent/tool/flow APIs unavailable')
        if not callable(getattr(crewai_event_bus, 'scoped_handlers', None)) or not callable(getattr(crewai_event_bus, 'flush', None)):
            raise RuntimeError('required public CrewAI event bus APIs unavailable')

    async def register_agent(self, agent: object, trusted_agent_id: str):
        self._loop = asyncio.get_running_loop()
        client = await super().register_agent(agent, trusted_agent_id)
        if hasattr(agent, 'tools'):
            # Host replaces the agent's tools: the gateway wrapper is its only capability.
            agent.tools = [self.make_tool(agent)]
        return client

    def _submit_from_any_thread(self, client: Any, parsed: ToolInvocation) -> ToolResult:
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError('gateway owner loop unavailable; nothing submitted')
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise RuntimeError('synchronous CrewAI tool call on the owner loop; use await tool.arun(...)')
        future = asyncio.run_coroutine_threadsafe(client.execute_protected_tool(parsed), loop)
        return future.result(timeout=TOOL_DISPATCH_TIMEOUT_SECONDS)

    def make_tool(self, agent: object) -> Any:
        from crewai.tools import BaseTool
        from pydantic import BaseModel
        client = self.runtime.client(agent)
        adapter = self

        class GatewayArguments(BaseModel):
            invocation: ToolInvocation

        class GatewayTool(BaseTool):
            name: str = GATEWAY_TOOL
            description: str = ('Propose one simulated SwarmGuard operation. The trusted gateway decides; '
                                'approval and resource-version checks are mandatory.')
            args_schema: type[BaseModel] = GatewayArguments
            cache_function: Any = lambda *_: False  # never replay a cached gateway outcome

            def _record(self, invocation: Any, result: ToolResult) -> str:
                adapter._results[result.tool_call_id or invocation.tool_call_id] = result
                return result.model_dump_json()

            def _parse(self, invocation: Any) -> ToolInvocation:
                if isinstance(invocation, ToolInvocation):
                    return invocation
                if isinstance(invocation, str):
                    invocation = json.loads(invocation)
                return ToolInvocation.model_validate(invocation)

            def _run(self, invocation: Any) -> str:
                parsed = self._parse(invocation)
                return self._record(parsed, adapter._submit_from_any_thread(client, parsed))

            async def _arun(self, invocation: Any) -> str:
                parsed = self._parse(invocation)
                if asyncio.get_running_loop() is adapter._loop:
                    return self._record(parsed, await client.execute_protected_tool(parsed))
                return await asyncio.to_thread(self._run, parsed)

        return GatewayTool()

    def _capture(self, name: str) -> Callable[[Any, Any], None]:
        def handler(source: Any, event: Any) -> None:
            with self._native_lock:
                self._native.append((name, event))
        return handler

    def _flush_native(self) -> None:
        from crewai.events import crewai_event_bus
        crewai_event_bus.flush(timeout=10)
        with self._native_lock:
            captured, self._native = self._native, []
        by_native_id = {str(getattr(obj, 'id', '')): obj for obj in self._agents.values()}
        def stamp(item: tuple[str, Any]) -> datetime:
            ts = getattr(item[1], 'timestamp', None)
            return ts if isinstance(ts, datetime) else datetime.min
        for name, event in sorted(captured, key=stamp):
            native_agent = getattr(event, 'agent_id', None)
            agent_obj = getattr(event, 'agent', None)
            # Attribute only to host-registered objects; role strings are never identity.
            owner = next((o for o in self._agents.values() if agent_obj is o), None) or by_native_id.get(str(native_agent))
            if owner is None:
                self.unattributed_native_events += 1
                continue
            aid = self.runtime.mapping.resolve(owner)
            ts = getattr(event, 'timestamp', None)
            args = getattr(event, 'tool_args', None)
            call_id = None
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            if isinstance(args, dict) and isinstance(args.get('invocation'), dict):
                raw_id = args['invocation'].get('tool_call_id')
                call_id = raw_id if isinstance(raw_id, str) and 1 <= len(raw_id) <= 128 else None
            self.runtime.event(aid, NATIVE_EVENTS[name], source='FRAMEWORK_REPORTED', tool_call_id=call_id,
                tool=getattr(event, 'tool_name', None), metadata={'native_event': name, 'surface': 'crewai_event_bus',
                'native_timestamp': ts.isoformat() if isinstance(ts, datetime) else None})

    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        """Drive one proposal through a real CrewAI Agent/Task/Crew execution.

        Requires an agent whose LLM is ``scripted_llm()``; live-model applications
        call ``crew.kickoff`` themselves with ``make_tool(agent)`` as the only tool.
        """
        from crewai import Crew, Process, Task
        from crewai.events import crewai_event_bus, ToolUsageStartedEvent, ToolUsageFinishedEvent, \
            ToolUsageErrorEvent, TaskStartedEvent, TaskFailedEvent, AgentExecutionStartedEvent, AgentExecutionErrorEvent
        llm = getattr(agent, 'llm', None)
        if not callable(getattr(llm, 'enqueue', None)):
            raise RuntimeError('fixture invocation requires the offline scripted CrewAI LLM')
        self.runtime.mapping.resolve(agent)
        self._results.pop(invocation.tool_call_id, None)
        action = json.dumps({'invocation': invocation.model_dump()})
        llm.enqueue(f'Thought: I will propose the operation to the gateway.\nAction: {GATEWAY_TOOL}\nAction Input: {action}',
                    'Thought: I now know the final answer\nFinal Answer: Gateway decision received.')
        task = Task(description='Submit the prepared synthetic operation through the SwarmGuard gateway tool.',
                    expected_output='The gateway decision.', agent=agent)
        native = {'TaskStartedEvent': TaskStartedEvent, 'AgentExecutionStartedEvent': AgentExecutionStartedEvent,
                  'ToolUsageStartedEvent': ToolUsageStartedEvent, 'ToolUsageFinishedEvent': ToolUsageFinishedEvent,
                  'ToolUsageErrorEvent': ToolUsageErrorEvent, 'AgentExecutionErrorEvent': AgentExecutionErrorEvent,
                  'TaskFailedEvent': TaskFailedEvent}
        with crewai_event_bus.scoped_handlers():
            for name, event_type in native.items():
                crewai_event_bus.on(event_type)(self._capture(name))
            try:
                await Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False,
                           tracing=False).kickoff_async()
            finally:
                self._flush_native()
        result = self._results.pop(invocation.tool_call_id, None)
        if result is None:
            raise RuntimeError('CrewAI did not dispatch the gateway tool; no execution assumed')
        return result

    async def run_pipeline(self, stages: list[tuple[object, Callable[[], Awaitable[None]]]]) -> None:
        from crewai.flow.flow import Flow, start, listen
        from swarmguard.integrations.event_normalizer import FrameworkEvent
        methods: dict[str, Any] = {}
        previous = None
        for index, (_, operation) in enumerate(stages):
            async def step(self, *args: Any, operation: Callable[[], Awaitable[None]] = operation) -> int:
                await operation()
                return 1
            name = f'stage_{index}'
            step.__name__ = name
            methods[name] = start()(step) if previous is None else listen(previous)(step)
            previous = name
        flow = type('SwarmGuardSyntheticFlow', (Flow,), methods)(tracing=False, suppress_flow_events=True)
        if stages:
            native_id = flow.state.get('id') if isinstance(flow.state, dict) else flow.state.id
            await self.normalize_event(stages[0][0], FrameworkEvent(event_type='AGENT_ACTIVITY',
                metadata={'native_flow_id': str(native_id) if native_id is not None else None, 'surface': 'CrewAI Flow'}))
        await flow.kickoff_async()

    async def close(self) -> None:
        if self._initialized:
            self.update_record(unattributed_native_events=self.unattributed_native_events)
        await super().close()
