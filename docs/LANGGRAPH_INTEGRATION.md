# LangGraph Integration

Tested: `langgraph` 1.2.20, `langgraph-prebuilt` 1.1.1, `langgraph-checkpoint` 4.3.1,
`langchain-core` 1.7.6 on CPython 3.13.18 and 3.14.4. Module: `swarmguard/integrations/langgraph/adapter.py`.
Public APIs only: `StateGraph`, `START`/`END`, `langgraph.prebuilt.ToolNode`, `InMemorySaver`,
`langgraph.types.interrupt`/`Command` (example), `langchain_core.tools.StructuredTool`, `AIMessage`/`ToolMessage`.

## Enforced mode

- `make_tool(agent)` returns a `StructuredTool` named `swarmguard_gateway` bound to that agent's credential.
- `tool_graph(agent)` compiles `START → ToolNode([gateway tool]) → END`. Proposals arrive as
  `AIMessage.tool_calls` exactly as a model-driven graph would carry them. `ToolNode` resolves and
  dispatches them; **an unregistered tool name (e.g. `send_email` called directly) is refused by
  ToolNode with an error `ToolMessage` and never reaches any executor** (tested).
- Inner tool graphs compile with `checkpointer=False` so they never inherit or accumulate outer state.

Mapping: graph execution → SwarmGuard workflow (`thread_id` = workflow ID); node → host-registered
owner object → trusted identity; `AIMessage.tool_calls[].id` → `tool_call_id` → gateway `request_id`.

## Observability

`run_pipeline` compiles the stage graph with `InMemorySaver`, streams `stream_mode="updates"`, and
records one `AGENT_ACTIVITY` (`native_event="StateGraph.updates"`) per node update with the node name,
state update and current `checkpoint_id`. Checkpoint references are also saved on the integration
record (`checkpoint_refs`). These are untrusted observations.

## Human interrupts

`examples/langgraph_example/example.py` pauses the graph with `interrupt()` after `request_approval`.
The interrupt only **pauses** LangGraph. The trusted host approves through SwarmGuard's approval
manager and then resumes with `Command(resume=...)`. A resume value grants nothing; the gateway
re-validates the stored approval.

## Limitations

- The benchmark uses scripted `AIMessage.tool_calls`; no LLM-bound `create_react_agent` was run live.
- An application can register other tools on another ToolNode; only the gateway tool is controlled.
- `InMemorySaver` checkpoints are process-local and not part of the SwarmGuard audit store.
