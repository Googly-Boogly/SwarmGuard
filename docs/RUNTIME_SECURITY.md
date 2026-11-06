# Stage 6 Runtime Security

Principle: **agents propose, monitors advise, trusted deterministic code authorizes, the human
operator approves.** Stage 6 adds framework adapters on top of the existing gateway. It adds no new
authorization logic and no new execution path.

## Trusted execution boundary

```
framework agent / model ──(proposal only)──> gateway-bound framework tool
   └─ SwarmGuardClient (slotted, bound to one credential + workflow; no other methods)
        └─ IntegrationRuntime.bind.submit   (host-owned; audit budget, workflow binding, tool_call_id → request_id)
             └─ AgentGatewayClient.submit → CapabilityGateway.submit → PolicyEngine
                  → [optional council / WorkflowGuard: ALLOW → ESCALATE only]
                  → MockToolExecutor.execute (HMAC permit, compare-and-set, approval consumption)
```

- The executor, database, workspace, approval manager, policy and credentials stay inside the
  simulator/gateway. `TrustedToolRegistry` exposes **metadata copies** only. It has no `register`,
  `execute` or mutation methods, so agents cannot add tools, change permissions, replace executors
  or disable approval.
- A framework `tool_call_id` becomes a deterministic gateway `request_id`, so a replayed or cached
  framework call is a gateway replay (BLOCK), never a second execution. CrewAI caching is disabled
  on the wrapper (`cache_function` returns `False`).
- Gateway submissions are shielded from task cancellation, so a proposal always gets its persisted
  decision. Audit capacity is checked *before* submission; if it is exhausted, nothing is submitted.
- CrewAI runs tools in worker threads. The wrapper hands each submission back to the owner event loop
  (`run_coroutine_threadsafe`) and never runs gateway code on a foreign thread or loop.
- AutoGen `ToolException` derives from `BaseException`; the adapter converts it so fail-closed
  handlers still apply.

## Framework identity mapping

Authorization identity comes only from the credential bound at host registration
(`TrustedAgentContext`). `IdentityMapping` uses object identity (`is`), not equality, names or
roles. Native framework events are attributed only when they reference a registered object
(CrewAI: `event.agent is obj` or the CrewAI `agent.id` of a registered agent; AutoGen: the recipient
`AgentId.type` registered by the host; LangGraph: the host-defined node owner). Everything else is
dropped and counted. Proposal fields such as `agent_id`, `role` or `trusted_context` are rejected by
schema, or audited as INVALID by the gateway when they come through the raw `submit_proposal` path.

## Observability versus enforcement

| Claim | Requires |
|---|---|
| "SwarmGuard observed X" | a canonical event (any source) |
| "SwarmGuard authorized/blocked X" | a persisted `DecisionRecord` (`GATEWAY_VERIFIED`) |
| "X executed" | gateway `executed=True` plus the executor's outcome (e.g. the simulated outbox) |

Observability-only integrations (`mode=OBSERVABILITY`) declare `tool_call_interception`,
`trusted_execution_wrapping` and `human_approval_integration` as `false` and refuse tool submission.
Hook registration alone (event bus, intervention handler, stream) is **never** described as a
security boundary. The AutoGen intervention handler could drop messages, but SwarmGuard uses it for
observation only.

## In-process limitations

All adapters run in the same Python process as the agents. That keeps honest code honest, but it is
not isolation:

- Hostile code with full interpreter access can import the simulator, read memory, or monkey-patch
  modules. Slotted clients and name mangling are hygiene, not a sandbox.
- A framework agent can be given *other* tools by the application. SwarmGuard controls only the
  capabilities whose executors it owns. The enforced examples give each agent only the gateway tool
  and register no shell, code-execution or network tools.
- The executors are mocks. No networking, SMTP or email API is imported in `swarmguard/` (AST-tested).

## Remote integration requirements (not implemented)

A production deployment would need the gateway in a separate service or process with:
per-agent credentials issued by the host (not agent IDs), authenticated transport (mTLS or signed
tokens), server-side identity resolution, real executors holding credentials that agents never see,
network egress controls so agents cannot reach the protected service directly, and an approval UI
bound to authenticated human identities. Stage 6 provides none of these; it provides the in-process
reference integration and the tests that define the expected behaviour.

## Browser boundary

The console (`console_main:app`) exposes Stage 6 GETs and five authenticated POSTs: integration run,
framework evaluation, baseline designation, regression run and job cancel. All require the operator
session, exact local Origin, JSON and the CSRF token. Command models are strict: the browser cannot
submit agent identities, tools, approvals, policies, credentials or live model identifiers, and cannot
weaken the regression policy's prohibited-execution invariant. The original `main:app` stays GET-only.
