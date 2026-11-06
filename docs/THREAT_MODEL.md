# SwarmGuard Threat Model (Stage 1)

Scope: a single-process local simulation. The goal is to show that authorization does not depend on agent cooperation or on AI-based monitoring. This is **not** a production security boundary and has **not** been formally verified.

## Trusted components

The simulator, `IdentityRegistry`, `PolicyEngine` and `SecurityPolicy`, `ApprovalManager` (including its signing key), `CapabilityGateway`, `DecisionEngine`, `MockToolExecutor` and the tools, `SharedWorkspace`, `WorkflowMonitor`, `WorkflowGraphService`, the repositories and the database, plus `SimulatedHumanOperator`, which stands in for a real person.

## Untrusted inputs

- **All AI agents** and their cooperation. Any agent may be compromised, mistaken or colluding.
- **Every field of every proposal**: tool, parameters, resource ID and version, parent actions, approval and request IDs, claimed identity, free text.
- **Ethics monitors.** They are advisory and can be wrong, fail, time out, or be manipulated through agent-written text.

## Security boundaries

A = agent-editable workspace, B = trusted security state, C = trusted audit state (see ARCHITECTURE §2). Agents cross into A only through gateway-executed tools, and never touch B or C.

## Threats and mitigations

| Threat | Mitigation | Tests |
|---|---|---|
| **Agent impersonation** | Identity comes only from the credential, which is passed separately from the proposal. A claimed `agent_id` that doesn't match → `INVALID_AGENT_IDENTITY`. Forged or absent credentials are rejected. A `role` claim → `INVALID_ACTION` | `test_agents::test_4_*`, `test_identity` |
| **Privilege escalation** | Immutable role policy; unknown tools rejected; sealed registries; policy document not writable | `test_agents::test_3_*` |
| **Approval laundering**: approval for X, then execution of X′ after (multi-agent) edits | The approval is bound to the exact operation, parameters, resource ID **and version**, workflow and approval ID. Edits bump the version and invalidate the approval. Send parameters must equal the stored version | `test_approvals::test_16/17/*revert*`, `test_integration::test_37` |
| **Fabricated or forged approvals** | Approvals exist only in the trusted store. Agents pass IDs only; approval objects in parameters → `INVALID_ACTION`; unknown IDs → `APPROVAL_NOT_FOUND`. Only `ApproverCredential` can grant, and agent IDs can't be registered as approvers | `test_15`, `test_agents::test_5`, `test_agents_cannot_approve_*` |
| **Parameter substitution** | The bound content comes from the stored draft, not the agent. The operator must approve the reviewed `operation_hash` | `test_request_binds_trusted_stored_content`, `test_agents_cannot_approve_*` |
| **Replay** | Single-use approvals (conditional UPDATE, unique `consumed_by_action_id`, unique `sent_emails.approval_id`); `request_id` reuse → `REPLAYED_REQUEST`; single-use execution permits | `test_20`, `test_replayed_request_ids_rejected`, `test_tools_cannot_be_invoked_*` |
| **Cross-workflow reuse** | Approvals, resources and parent actions are workflow-scoped → `WORKFLOW_MISMATCH` | `test_21`, `test_unauthorized_workflows_rejected` |
| **Shared-resource manipulation** | Role and ACL checks in both the policy engine and the workspace; full version snapshots with the responsible agent | `test_workspace` |
| **Resource races / TOCTOU** | Compare-and-set versions. Protected execution re-validates the version, content and approval inside one `BEGIN IMMEDIATE` transaction and rolls back on mismatch. Concurrent consumers: exactly one wins, both across asyncio interleaving and across separate SQLite connections | `test_9`, `test_execution_rechecks_state_and_rolls_back`, `test_23_*` |
| **Gateway bypass** | Agents hold no workspace, database or executor reference. Tools require an HMAC permit keyed inside the gateway, and every tool re-verifies it | `test_tools_cannot_be_invoked_outside_the_gateway` |
| **Monitor failure or override** | Policy BLOCK is final; advice only escalates; failed monitors → ESCALATE, never ALLOW | `test_ethics::test_35/36` |
| **Internal errors** | Fail closed (`INTERNAL_ERROR`, nothing executed, decision persisted) | `test_22_*` |
| **Unrecorded blocks** | Every submission, including malformed and unauthenticated ones, gets a persisted decision | `test_malformed_proposals_rejected_and_recorded`, `test_27` |
| **Real-world side effects** | No networking imports in the package (AST test); `send_email` writes SQLite only; reserved example domains enforced; demo passes with sockets disabled | `test_40`, `test_package_contains_*` |
| **API misuse** | GET-only routes; SQLite `mode=ro`; path parameters validated; no SQL accepted; binds 127.0.0.1 by default | `test_api` |

## Audit integrity limitations

- The SQLite log is a **trusted local simulation log, not a tamper-proof ledger**. Anyone with filesystem access can edit events, decisions or resources.
- Approval rows carry an HMAC signature. An attacker who edits a row and recomputes the unkeyed SHA-256 digests is still detected (`test_tampered_approval_record_detected`). The key lives in process memory, though, so anyone with in-process access can read it. If no key is configured, a random one is generated per process, and approvals from earlier runs then fail closed after a restart.
- Events and decisions are not hash-chained. Detecting deleted or rewritten audit rows needs a hash chain, external anchoring, or write-once storage (future work).

## Remaining security assumptions

1. **In-process isolation only.** Agents share the interpreter with the gateway. A hostile *Python* agent could use introspection (name-mangled attributes, `gc`) to reach trusted objects. Stage 1 agents are scripted and do not. Real isolation needs separate processes, an RPC boundary, and signed per-request tokens.
2. **Simulated human.** The operator approves whatever the trusted script presents. A real UI, authentication of the human, and reviewer fatigue are out of scope.
3. **Agent-authored text** (document content, email bodies, event summaries) appears in the monitor context. Stage 2 LLM monitors face prompt injection from it. History must be delimited as data and must never influence authorization.
4. **Policy coverage** is limited to the simulated tools. Harms the rules don't model (e.g. an approved but harmful message) are not caught deterministically; detecting those is what the ethics research is for.
5. **Single process, one SQLite file.** Cross-connection atomicity of approval consumption is tested. Multi-host deployment is not supported.
6. **The mock ethics monitors** are fixed heuristics tied to the demo scenario. They are not evidence about ethical monitoring.
