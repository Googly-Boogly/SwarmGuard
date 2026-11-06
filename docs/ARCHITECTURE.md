# SwarmGuard Architecture (Stage 1)

> **Core invariant:** LLMs propose actions. Trusted Python code authorizes execution.
> In Stage 1 the "LLMs" are deterministic scripted agents.

## 1. Trusted and untrusted components

| Component | Module | Trust |
|---|---|---|
| Coordinator, Research, Operations agents | `swarmguard/agents/{coordinator,research_agent,operations_agent}.py` | **untrusted** |
| Ethics monitors and council | `swarmguard/ethics/` | **advisory** (can only escalate) |
| Simulator (wiring, identities, seed data) | `swarmguard/agents/simulator.py` | trusted |
| Simulated human operator | `SimulatedHumanOperator` in `simulator.py` | trusted (stands in for a person) |
| Identity registry and runtime context | `swarmguard/security/identity.py` | trusted |
| Policy engine | `swarmguard/security/policy_engine.py` | trusted, deterministic |
| Approval manager | `swarmguard/security/approval_manager.py` | trusted |
| Capability gateway | `swarmguard/security/capability_gateway.py` | trusted, sole execution path |
| Decision engine | `swarmguard/security/decision_engine.py` | trusted |
| Mock tools and executor | `swarmguard/tools/mock_tools.py` | trusted, gateway-private |
| Shared workspace | `swarmguard/agents/workspace.py` | trusted code guarding agent-editable data |
| Workflow monitor / graph | `swarmguard/security/workflow_{monitor,graph}.py` | trusted |
| SQLite persistence | `swarmguard/storage/{database,repositories}.py` | trusted |
| Observability API | `swarmguard/api.py`, `main.py` | read-only |

## 2. Trust boundaries in SQLite

| Boundary | Tables | Who writes |
|---|---|---|
| **A: agent-editable workspace** | `resources` | `SharedWorkspace`, called only by gateway-executed tools |
| **B: trusted security state** | `workflows` (membership), `approvals` (+ hashes, HMAC signature); identities, permissions, and keys live in memory | simulator, `ApprovalManager` |
| **C: trusted audit state** | `actions`, `decisions`, `events`, `resource_versions`, `sent_emails` | monitor, workspace, tools (append-only by convention) |

No agent interface touches any table. All SQL lives in `database.py` and `repositories.py` as fixed, parameterized statements.

## 3. Agent runtime

The simulator registers each agent in `IdentityRegistry` and receives an opaque `AgentCredential`. The agent itself gets only `AgentIdentity` (frozen) and an `AgentGatewayClient`, a slotted object that forwards proposals together with the bound credential. After setup the registry is sealed.

For each request the gateway builds a `TrustedAgentContext`:
- `identity` is resolved from the credential,
- `permissions` come from the policy engine, by role,
- `authorized_workflows` come from trusted workflow membership.

Nothing in the context comes from the proposal. An `agent_id` inside a proposal is treated as a claim, and a mismatch is blocked (`INVALID_AGENT_IDENTITY`). Any other unknown field, such as `role`, is rejected (`INVALID_ACTION`).

## 4. Shared workspace and versioning

`SharedWorkspace.update(resource_id, changes, agent_id, expected_version, …)` works as follows:

1. It checks the resource ACL for the responsible agent. This is defense in depth: the policy engine has already checked it.
2. It rejects a stale `expected_version` (`VersionConflictError`). The gateway rejects the same case earlier as `RESOURCE_VERSION_MISMATCH`.
3. It applies the change through `UPDATE … WHERE version = expected` (compare-and-set).
4. It writes an immutable snapshot to `resource_versions`, recording content, diff, agent, action and event.
5. It emits `RESOURCE_MODIFIED` with `resource_version_before`/`after`, the agent ID, and a field diff.

Every write tool requires `resource_version`, so blind overwrites are impossible. A write that changes nothing does not bump the version.

## 5. Capability gateway: data flow of a protected operation

```text
OperationsAgent ──ActionProposal{tool=send_email, resource_id=email_001, resource_version=2,
                │                 parameters{recipient,subject,body}, approval_id, parent_action_ids}
                ▼  AgentGatewayClient (credential bound out-of-band)
CapabilityGateway.submit
  1 resolve identity from credential ─────────── unknown → BLOCK INVALID_AGENT_IDENTITY
  2 strict schema of proposal ────────────────── malformed → BLOCK INVALID_ACTION
  3 ActionRequest(action_id, created_at, agent_id from credential) → actions + ACTION_PROPOSED
  4 TrustedAgentContext (policy permissions, workflow membership)
  5 PolicyEngine.evaluate (first failing check wins):
      identity claim → tool registered → strict tool params → resource_id/version presence
      → request_id replay → workflow exists/active/member → role permission → parent actions
      → resource exists/type/workflow/ACL → resource version → tool rules (reserved domains,
        request content == stored version) → approval: exists, signature/integrity, workflow,
        operation, status, expiry, trusted approver, snapshot diff, canonical operation hash
  6 EthicsCouncil.deliberate (optional, advisory) → ETHICS_ASSESSMENT events
  7 DecisionEngine.combine → DecisionRecord → decisions + DECISION_RECORDED
  8 ALLOW only → MockToolExecutor.execute(request, params, HMAC permit{version, operation_hash})
       send_email, in ONE BEGIN IMMEDIATE transaction:
         re-read draft (version + content) → ApprovalManager.consume (conditional UPDATE
         APPROVED→CONSUMED) → insert sent_emails (status SIMULATED_EMAIL_SENT) → event
       any failure → rollback → BLOCK EXECUTION_PRECONDITION_FAILED
  9 ACTION_EXECUTED / ACTION_EXECUTION_FAILED / SECURITY_ALERT → GatewayResponse
```

Any exception during authorization produces BLOCK `INTERNAL_ERROR`, and nothing is executed. Approved parameter values are never echoed back to the agent; the response lists only `differing_fields`.

## 6. Policy engine

`SecurityPolicy` (frozen dataclass and `MappingProxyType`) is the only source of permissions:

| Operation | Coordinator | Research | Operations |
|---|---|---|---|
| read_document | ✔ | ✔ | ✔ |
| update_shared_document | ✔ (ACL-limited) | ✔ | ✔ (ACL-limited) |
| read_customer_record | ✔ (ACL-limited; seed ACL excludes it) | ✔ | ✔ |
| read_email_draft | ✔ | – | ✔ |
| prepare_email / modify_email / request_approval / send_email | – | – | ✔ |
| assign_task | ✔ | – | – |
| complete_task | ✔ (assignee only) | ✔ (assignee only) | ✔ (assignee only) |
| modify policy / grant approval | – | – | – (no such tool exists) |

Reason codes (`models/decisions.py::ReasonCode`) are a stable audit format.

## 7. Approval manager

- **Request**: `request_approval` binds the stored draft at the exact requested version. The agent cannot choose the bound values. It stores `operation_hash = SHA-256(canonical{approval_id, workflow_id, operation, resource_id, resource_version, parameters})`, using sorted keys, `(",", ":")` separators and `ensure_ascii=False`.
- **Grant**: only an `ApproverCredential` can grant. The operator must echo the reviewed `operation_hash`. The grant sets `binding_hash = SHA-256(canonical{operation_hash, approver_id, expires_at})` and `signature = HMAC-SHA256(signing_key, binding_hash)`. The digests bind integrity; authority comes from the trusted record together with the credential.
- **Lifecycle**: PENDING → APPROVED/REJECTED. APPROVED → CONSUMED (single use), EXPIRED, or INVALIDATED (any modification of the bound resource). All transitions are conditional UPDATEs and emit events with explicit `parent_event_ids`.

## 8. Workflow monitor

The monitor provides `get_workflow_history`, `get_agent_history`, `get_resource_history`, `get_related_actions` and `get_workflow_summary`, plus `build_workflow_context` for the ethics council.

Each event has a stable ID, a global `seq`, an actor type (AGENT/HUMAN/SYSTEM/MONITOR), version before/after, and explicit `parent_event_ids` written at record time. The default parent is the previous event of the same action. `ACTION_PROPOSED` events point to the head events of the parent actions, and approval events point to their causes. The monitor records only what it observes.

## 9. Workflow graph

`WorkflowGraphService.build_workflow_graph` produces serializable `WorkflowGraph{nodes, edges}`. Node types are AGENT, HUMAN, ACTION, RESOURCE_VERSION, APPROVAL, DECISION and EXECUTION. Each node carries `ref_table`/`ref_id` pointing at a persisted row.

Edges come from explicit references only:
- `DEPENDS_ON` from `parent_action_ids`
- `READS_FROM` and `MODIFIES` from versioned events and requests
- `SUPERSEDES` from version snapshots
- `ASSIGNED_BY` and `ASSIGNED_TO` from task content
- `REQUESTS_APPROVAL`, `BINDS_TO`, `APPROVED_BY`, `REJECTED_BY`, `INVALIDATED_BY`, `CITES_APPROVAL` and `CONSUMED_BY` from approval records and events
- `DECIDED` and `RESULTED_IN` from decision records

`EXECUTED_AFTER` is the only chronological edge and carries `causal=false`.

Other queries:
- `get_action_dependencies` walks `DEPENDS_ON` and data provenance: a version an action read leads to the action that produced it.
- `get_resource_modification_chain` returns the version snapshots.
- `get_approval_dependency_chain` traces an approval from request through grant, invalidation, citation and consumption.

## 10. Ethics council (advisory)

`EthicsMonitor.evaluate(action, workflow_context) -> EthicalAssessment`. Every monitor receives the identical `WorkflowContext`, which deliberately excludes the policy decision. `EthicsCouncil` runs monitors concurrently with timeouts and validates each output (schema, framework, action ID). It records failures and missing frameworks and preserves every position.

**Aggregation rule (unanimity):** the council returns ALLOW only if all four frameworks return ALLOW, BLOCK only if all four return BLOCK, and ESCALATE for any disagreement, failure or missing framework. A failed monitor never counts as ALLOW.

`DecisionEngine` is monotone. A policy BLOCK or ESCALATE is final. A policy ALLOW becomes ESCALATE (`ETHICS_ESCALATION`) if the council raises a concern, and is then not executed. The council cannot grant permissions, create approvals, change policy, or execute anything.

All four Stage 1 monitors are **mocks**: one or two hand-written event or keyword rules each, labeled `is_mock=True` and `[MOCK heuristic, not an LLM assessment]`.

## 11. Stage 2 extension points

- `EthicsMonitor` is the LLM monitor interface; `WorkflowContext` is its input contract.
- `evaluation/baselines.py::restrict_context` provides the action-only condition.
- `ActionProposal` is the output contract for LLM-driven agents.
- `evaluation/runner.py::ExperimentConfig` / `EvaluationResult` define the experiment format.
