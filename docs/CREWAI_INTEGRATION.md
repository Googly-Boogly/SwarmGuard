# CrewAI Integration

Tested: `crewai` 1.15.36 on CPython 3.13.18 (CrewAI 1.15 does not support Python 3.14).
Module: `swarmguard/integrations/crewai/adapter.py`. Public APIs only: `crewai.Agent`, `Task`,
`Crew`, `Process`, `BaseLLM`, `crewai.tools.BaseTool`, `crewai.flow.flow.Flow/start/listen`,
`crewai.events.crewai_event_bus` (`on`, `scoped_handlers`, `flush`). No private patches.

## Enforced mode

1. The host builds `Agent` objects and calls `await guard.register_agent(agent, "operations_agent")`.
2. Registration **replaces `agent.tools`** with one `BaseTool` named `swarmguard_gateway`, bound to that
   agent's credential. Its `args_schema` is `{"invocation": ToolInvocation}` (strict).
3. CrewAI's own agent loop parses the model's ReAct `Action` / `Action Input`, validates the arguments
   and calls the tool. CrewAI calls `_run` from a worker thread; the wrapper forwards the proposal to the
   gateway on the owner event loop and returns the `ToolResult` JSON to the agent.
4. The gateway decides. A forged or stale approval is BLOCKed and nothing executes.

`adapter.invoke(agent, invocation)` drives one proposal through a real single-task `Crew.kickoff_async`.
It requires the offline `scripted_llm()` (a labelled synthetic `BaseLLM` that emits the ReAct text);
live applications call `crew.kickoff()` themselves with the same tool. `run_pipeline` orders the
coordinator → research → operations stages with a CrewAI `Flow`.

## Observability

Within each kickoff a scoped event-bus listener records `TaskStartedEvent` → `TASK_ASSIGNED`,
`AgentExecutionStartedEvent` → `AGENT_ACTIVITY`, `ToolUsageStartedEvent` → `TOOL_CALL_PROPOSED`,
`ToolUsageFinishedEvent` → `TOOL_EXECUTED`, `ToolUsageErrorEvent` → `TOOL_FAILED`, and
`AgentExecutionErrorEvent`/`TaskFailedEvent` → `WORKFLOW_ERROR`. All are `FRAMEWORK_REPORTED`
with `metadata.native_event`. CrewAI dispatches handlers asynchronously (Finished can arrive before
Started), so events are buffered, flushed, sorted by native timestamp, and attributed only to
registered agents. A CrewAI "tool finished" event is not execution proof; the gateway decision is.

## Measured coverage (results/stage6, swarmguard_core, CrewAI 1.15.36)

Per service-case workflow (9 gateway tool calls): `AgentExecutionStartedEvent` 9, `ToolUsageStartedEvent` 9,
`ToolUsageFinishedEvent` 9. **`TaskStartedEvent` was subscribed but never delivered to the scoped listener**,
so CrewAI-native `TASK_ASSIGNED` coverage is zero. Task assignment is still visible through gateway-verified
`assign_task` decisions. No tool-usage error events occurred in these fixtures.

## Limitations

- The event bus is process-global; concurrent crews in one process may interleave events.
- The benchmark "model" is a deterministic script. CrewAI prompt formatting, ReAct parsing, tool
  selection and dispatch are real; live LLM behaviour is untested.
- If an application gives a CrewAI agent other tools after registration, SwarmGuard does not control them.
- A synchronous `tool.run()` on the owner event loop thread is refused (it would deadlock); use `arun`.
