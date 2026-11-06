# AutoGen Integration

Tested: `autogen-core` 0.7.5 and `autogen-agentchat` 0.7.5 on CPython 3.13.18 and 3.14.4.
Only the 0.7 (Core) API generation is used; no `pyautogen` / 0.2-style `ConversableAgent` APIs are mixed in.
Module: `swarmguard/integrations/autogen/adapter.py`. Public APIs: `SingleThreadedAgentRuntime`,
`AgentId`, `FunctionCall`, `DefaultInterventionHandler`, `autogen_core.tool_agent.ToolAgent`,
`autogen_core.tools.FunctionTool`, `autogen_core.models.FunctionExecutionResult`.

## Enforced mode

- On registration the host registers a `ToolAgent` of type `<trusted agent id>` whose only tool is a
  `FunctionTool` named `swarmguard_gateway` bound to that agent's credential.
- Proposals travel as `FunctionCall(id, name, arguments)` messages via `runtime.send_message` to the
  host-owned `AgentId`. ToolAgent dispatches them. **Unknown tool names raise `ToolNotFoundException`
  and never reach an executor** (tested). No code-execution, shell or network agents are registered.
- AutoGen's `ToolException` hierarchy derives from `BaseException`; the adapter converts it to
  `RuntimeError` so fail-closed handlers apply and nothing is assumed executed.

## Observability

An intervention handler records `on_send(FunctionCall)` → `MESSAGE` and
`on_response(FunctionExecutionResult)` → `TOOL_EXECUTED`/`TOOL_FAILED`, as `FRAMEWORK_REPORTED`
events attributed only to host-registered `AgentId.type` values. It always returns the message
unchanged: it never drops, rewrites, approves or executes.

## Limitations

- The benchmark sends `FunctionCall` messages directly (a scripted model). AgentChat
  `AssistantAgent` with a live model client was not run.
- An application can register other ToolAgents or code executors in the same runtime; they are not controlled.
- `SingleThreadedAgentRuntime` is in-process; no distributed (gRPC) runtime was tested.
