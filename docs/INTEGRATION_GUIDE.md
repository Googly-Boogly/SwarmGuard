# Stage 6 Integration Guide

SwarmGuard Stage 6 lets agents built with an external framework propose operations to the
existing SwarmGuard gateway. **Connecting SwarmGuard does not make an application safe.** Protection
exists only for capabilities you actually route through the gateway-bound tools described here.

## Supported frameworks

| Framework | Package / tested version | Execution boundary used | Observation surface |
|---|---|---|---|
| Custom Python | none (Python 3.11+; tested 3.13.18, 3.14.4) | `SwarmGuardClient.execute_protected_tool` | host-reported events |
| CrewAI | `crewai` 1.15.36 (Python < 3.14 only) | gateway-bound `BaseTool`; real `Agent`/`Task`/`Crew` ReAct dispatch | `crewai_event_bus` (task, agent, tool-usage events) |
| LangGraph | `langgraph` 1.2.20, `langchain-core` 1.7.6 | gateway-bound `StructuredTool` inside a prebuilt `ToolNode` | `StateGraph.astream(stream_mode="updates")`, checkpoint IDs |
| AutoGen | `autogen-core` / `autogen-agentchat` 0.7.5 | gateway-bound `FunctionTool` in a `ToolAgent` on `SingleThreadedAgentRuntime` | `DefaultInterventionHandler.on_send/on_response` |

The registry (`swarmguard/integrations/registry.py`) detects the installed version at runtime
and reports `AVAILABLE`, `UNSUPPORTED` (installed, wrong version family) or `DISCONNECTED`
(not installed). An adapter instance records `INITIALIZING`, `CONNECTED`, `ERROR` and
`DISCONNECTED`. Detection is not verification: `verification` is updated only after a workflow
actually ran through the framework.

Only these version families were exercised. Other versions are reported `UNSUPPORTED`.

## Installation

```bash
pip install -e ".[test]"                 # core + tests; the custom adapter needs nothing else
pip install -e ".[langgraph]"            # LangGraph + LangChain Core
pip install -e ".[autogen]"              # AutoGen Core / AgentChat 0.7
pip install -e ".[crewai]"               # CrewAI 1.15 — requires Python < 3.14
pip install -e ".[integrations]"         # all three
```

CrewAI 1.15 does not install on Python 3.14. The repository was verified with two interpreters:
`.venv` (3.14: custom, LangGraph, AutoGen) and `.venv-stage6` (3.13: all four). Missing
optional dependencies never disable SwarmGuard or mandatory authorization; the adapter
initializes to `ERROR` and refuses to run.

Set `OTEL_SDK_DISABLED=true`, `CREWAI_TELEMETRY_ENABLED=false` and `CREWAI_TRACING_ENABLED=false`
to keep frameworks offline (the CLIs and tests do this). `stage6_cli.py` also points
`CREWAI_STORAGE_DIR` at the output directory.

## Agent registration and tool wrapping

The trusted host (your application process, not the model) does all of this:

```python
from swarmguard.sdk import LocalSwarmGuard, ToolInvocation

async with await LocalSwarmGuard.open(framework="custom", customers=SYNTHETIC_CUSTOMERS) as guard:
    agent = MyAgent()
    client = await guard.register_agent(agent, "operations_agent")   # host-owned object -> sealed identity
    result = await client.execute_protected_tool(ToolInvocation(
        tool="prepare_email", tool_call_id="call_1",
        parameters={"recipient": "customer@example.test", "subject": "Service Update",
                    "body": "Your service request has been received."}))
    print(result.gateway.decision, result.gateway.reason_code, result.gateway.executed)
```

- `register_agent(obj, trusted_id)` maps a **host-owned Python object** (by identity, `is`) to one
  of the simulator's sealed agent identities. Strings, dicts and other plain values are rejected;
  duplicate or ambiguous mappings are rejected; names, roles and framework metadata are never used.
- The agent receives only a bound client (`SwarmGuardClient`) or the framework tool from
  `guard.tool_for(agent)`. Neither exposes registration, approval, policy, database or executor.
- `ToolInvocation` is strict (`extra="forbid"`): it cannot carry `agent_id`, roles, credentials,
  executors or policies. A claimed foreign `workflow_id` is rejected and audited by the gateway.
- For CrewAI, registration replaces `agent.tools` with the single gateway tool.

Framework-specific details: [CrewAI](CREWAI_INTEGRATION.md), [LangGraph](LANGGRAPH_INTEGRATION.md),
[AutoGen](AUTOGEN_INTEGRATION.md). Complete runnable examples are in `examples/`.

## Event collection

All events are normalized to `CanonicalEvent` (`swarmguard/integrations/event_normalizer.py`) and
persisted per integration. Every event has a `source`:

- `HOST_REGISTERED`: written by trusted host code (registration, fixture completion).
- `GATEWAY_VERIFIED`: derived from a persisted gateway decision (`AUTHORIZATION_DECISION`,
  `TOOL_EXECUTED`/`TOOL_FAILED` with the gateway `action_id`).
- `FRAMEWORK_REPORTED`: anything the framework or model reported, including native framework
  events (marked with `metadata.native_event`). These are **untrusted observations**: they never
  become authorization identity, approval evidence or execution proof, and are recorded in the
  workflow history as `FRAMEWORK_OBSERVATION` only.

Causal references are never inferred. `parent_action_ids` comes only from gateway-recorded
proposals; absent references stay empty/`null`. Native events not attributable to a registered
object are dropped and counted (`unattributed_native_events`).

## Authorization and approval

Every protected call follows the unchanged Stage 1–4 path:
`SwarmGuardClient → AgentGatewayClient.submit → CapabilityGateway.submit → PolicyEngine →
MockToolExecutor.execute (HMAC permit)`. Approvals use the existing `ApprovalManager`: they bind
workflow, tool, exact protected parameters, resource ID and version, expiry and the trusted approver,
and are consumed with a conditional update. An approval from one workflow, framework or resource
cannot authorize another (tested). In the console, framework runs that need approval wait in the
existing **Human Approvals** page. There is no second approval system.

## Monitoring modes

`IntegrationConfig.monitoring`:

- `OBSERVE`: local and global monitor assessments are recorded and returned as advisories.
- `WARN`: same as OBSERVE. Advisories are returned to the host; nothing changes execution.
- `ESCALATE` / `ENFORCE`: require the existing global `WorkflowGuard` at simulator construction;
  it can only turn ALLOW into ESCALATE (`WORKFLOW_RISK_ESCALATION`).

`IntegrationConfig.mode`: `ENFORCED` (the gateway-bound tool is the protected path) or
`OBSERVABILITY` (events only; tool submission refuses and all interception/wrapping capabilities
are declared `false`). LLM or council ALLOW/BLOCK output is never an authoritative policy decision.

## Configuration precedence

1. Code constants: policy, tool specs, `RegressionPolicy` invariants (cannot be overridden).
2. Host constructor arguments (`IntegrationConfig`, simulator limits). Frozen, strict, no credentials.
3. CLI flags / authenticated console commands. Validated by the same strict models.
4. Agents and models: **no configuration authority**.

## Known limitations

- In-process only. Hostile Python code in the same process can bypass any in-process wrapper
  (see [RUNTIME_SECURITY.md](RUNTIME_SECURITY.md)). There is no remote transport.
- Enforcement covers only tools you route through SwarmGuard. A framework agent given another tool
  (shell, HTTP, a second email client) is not controlled.
- The benchmark agents use deterministic scripted "models" (CrewAI `BaseLLM` script, LangGraph
  `AIMessage.tool_calls`, AutoGen `FunctionCall`). Framework dispatch is real; LLM behaviour is not
  tested. No live LLM framework run has been performed.
- CrewAI's event bus is process-global; concurrent crews in one process can see each other's
  events (only events attributable to registered agents are kept).
- Event coverage differs by framework and is measured, not assumed (see `native_event_coverage`).
