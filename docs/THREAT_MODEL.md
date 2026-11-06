# SwarmGuard Threat Model (Stages 1–3)

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

## Stage 2: workflow-level threats

Stage 1 shows that an approval binds exactly one operation. Stage 2 addresses failures that **no single authorization check can see**, because every individual check passes.

| Threat | Example scenario | What Stage 1 does | What Stage 2 adds | Tests |
|---|---|---|---|---|
| **Distributed approval laundering**: individually authorized steps, plus valid approvals, combine into a prohibited outcome | `distributed_approval_001/002` | ALLOW. Every check passes and the approval is exact | Global monitor traces the protected values from record → brief → outbound and evaluates invariant A/E. Optional guard escalates | `test_19`, `test_13`, `test_guard_escalation_*` |
| **Cumulative authorization failure**: each disclosure is permitted, the sum is not | `task_decomposition_001` | ALLOW both sends | Invariant C counts quasi-identifiers across observable sends | `test_cumulative_*` |
| **Shared-memory manipulation**: an agent edits shared state that another agent consumes | `shared_memory_001` | ACL + versioning + attribution | Provenance through the shared document; cross-agent change reported (never a violation by itself) | `test_20`, `test_14`, `test_23` |
| **Cross-agent data flow / purpose drift** | `purpose_violation_001` | Not modeled | Invariant B, using the declared workflow purpose vs. record consent | `test_purpose_*` |
| **Task decomposition**: a prohibited objective split into benign-looking tasks | `task_decomposition_001` | Each task is authorized | Heuristic `SUSPICIOUS_TASK_DECOMPOSITION` annotation on cumulative violations | `test_21`, detector tests |
| **Monitoring information leakage**: monitors see labels, scenario names or future events | – | n/a | Single builder; as-of snapshots with replayed approvals; neutral titles and IDs; ground truth read only after prediction; monitors reject over-wide contexts; evidence must be in context | `test_11`, `test_17*`, `test_26`, `test_detectors_do_not_depend_on_identifiers` |
| **Monitor side effects** | – | n/a | Monitors hold no storage handle; runner fingerprints the log around evaluation | `test_39`, `test_runner_detects_monitor_side_effects` |
| **Monitor/guard overriding deterministic authorization** | – | Council can only escalate | Guard runs only after policy ALLOW, can only escalate, fails closed | `test_40_*`, `test_guard_failure_fails_closed`, `test_34_36_*` |

### Limitations of local authorization (why Stage 2 exists)
- Role and ACL permissions and approval binding answer *"is this exact operation permitted and approved?"*, not *"what does this operation do in combination with the others?"*
- A human approver shown a single packet cannot see provenance. Distributed approval laundering exploits exactly that, without forging anything.

### Known Stage 2 weaknesses
- **Exact-value matching.** Paraphrased or partial values evade the data-flow detector (`transformed_disclosure_001` is a measured miss).
- **Workflow-scoped view.** Authorization changes recorded outside the workflow log are invisible, so the global window can be stale (`stale_authorization_001` is a measured false positive). Conversely, an adversary could route data through resources outside the log.
- **Precautionary local rule.** `UNVERIFIED_SENSITIVE_CONTENT` escalates any unverifiable sensitive-format token. That flags legitimate emails (measured false positive), and an adversary can evade it simply by reformatting.
- **Agent-authored text in contexts.** Event summaries, documents and email bodies are agent-controlled. Stage 3 LLM monitors face prompt injection through them, and must treat them as data.
- **Guard scope.** Enforcement covers `send_email` only. Drafting-time violations are observed, not enforced.
- **Same-author benchmark.** The detectors and scenarios share an author. See `docs/STAGE2_RESEARCH_DESIGN.md` §10.

## Stage 3: LLM monitor threats

| Threat | Mitigation | Tests |
|---|---|---|
| **Prompt injection** via agent-authored workflow text | Constant system prompts; data only as escaped JSON under `untrusted_workflow_data`; explicit "data, not instructions" rule; monitors are advisory and hold no capability | `test_20_*`, `test_46_*` |
| **Monitor "obeys" injection and says ALLOW** | ALLOW can never override a policy BLOCK or ESCALATE; the policy decides execution | `test_44_*`, `test_46_*` |
| **Evidence fabrication** (invented event IDs) | Assessment rejected (`INVALID_EVIDENCE`), recorded as a failure, counted in `evidence_reference_validity` | `test_17_*`, `test_36_*` |
| **Malformed / missing structured output** | Strict schema at the provider and revalidation in the monitor; recorded as `INVALID_RESPONSE` | `test_2_3_*`, `test_19_*` |
| **API failure bypassing authorization** | Failure leads to an incomplete council, which gives at least ESCALATE; a total council crash leaves the deterministic decision unchanged; never fail-open on policy | `test_45_*` |
| **Silent mock fallback** in research runs | No fallback path exists; missing live configuration stops before any request | `test_6_8_*`, `test_live_demo_refuses_*` |
| **API key exposure** | `SecretStr`; `repr=False`; key scrubbed from error messages; never in results or logs; `.env` gitignored | `test_7_*` |
| **Runaway spending / retries / concurrency** | Request cap; conservative pre-request cost reservation; bounded concurrency; retry cap ≤ 5 on retryable kinds only; SDK retries off | `test_budget_*`, `test_10_*`, `test_concurrency_*` |
| **Ground-truth leakage** to monitors | Same builder and neutral IDs; ground truth read after prediction; spy tests on prompts | `test_33_*` |
| **Network code in the trusted core** | OpenAI adapter isolated in `swarmguard_providers/`; the core never imports it; suite-wide socket block | `test_core_never_imports_*`, `test_55_*` |

### Remaining Stage 3 limitations
- Prompt separation does **not** guarantee injection resistance. Live models were not red-teamed.
- Evidence validation checks that IDs exist, not that the model read them correctly.
- Local budget accounting is an estimate and not a billing guarantee. Use provider-side limits.
- When a council is attached to the gateway, an LLM outage makes protected sends escalate. That is safe, but it is an availability cost.
- The live OpenAI path is tested only against the real SDK with a mock HTTP transport; it has **not** been exercised against the live API.


## Stage 4.5: untrusted browser boundary

Scope remains loopback, one operator, one backend worker, synthetic data. The console is not approved for public internet exposure. Read-only viewers can inspect synthetic workflow data; anyone able to reach loopback endpoints can read those records. Authentication protects mutations and WebSocket delivery, not every GET. Filesystem owners can alter SQLite and the local password file; audit records are not tamper-proof.

| Threat | Mitigation |
|---|---|
| Browser impersonates agent or human | Fixed server-derived operator identity, sealed existing registry, password-verified server session; no input identity/credential fields |
| Cross-site operator action / CSRF | SameSite=Strict cookie, exact local Origin check, JSON-only bodies, per-session CSRF header; no permissive credentialed CORS |
| Session theft/fixation | HttpOnly, Secure default, rotation at login, one-hour server expiry, server-side logout, restart revocation; no session token in JSON or URLs |
| Password disclosure / guessing | Local hidden-input provisioning, salted scrypt hash with 0600 file mode, no default password, failed-login rate limit |
| Approval parameter/version substitution | Exact packet review and explicit confirmation; transactional current resource/status/version/content/deadline/digest checks; existing bound/signed, expiring, single-use approval path |
| Agent self-approval | Agent credentials/bearer claims cannot authenticate the operator API; agents have no browser/network/control handle |
| Browser bypasses gateway | No raw tool, SQL, shell, identity creation, or policy editing endpoints; jobs route all agent proposals through existing credential-bound gateway |
| Runaway tasks or paid requests | Allowlisted bounded modes/configurations, one worker, bounded queue, step/runtime/request caps, cancellation, no auto-resume; browser live mode rejected even when confirmed |
| Lost/replayed live events | Persisted IDs and sequence cursors, reconnect plus polling, client deduplication; database is source of truth |
| WebSocket abuse | Session and Origin validation, workflow existence checks, eight-connection cap, documented 16 KiB Uvicorn limit; no client command protocol |
| Agent-generated XSS/instructions | React text/JSON rendering, no unsafe HTML, no generated-code execution; messages never alter permissions |
| Misleading safety/research presentation | Distinct mock/scripted/live labels, actual checks only, explicit execution state, preserved disagreements/failures, null denominators, no significance or LLM-performance claims |

Residual limitations: one-process cooperative scheduling, no TLS listener/proxy configuration supplied, no account recovery or multi-user RBAC, no cross-process job lock, no persistent approver-credential recovery, no per-agent pause/resume, and no browser live autonomous runner. A submitted action may finish before cooperative pause takes effect. On localhost Chromium accepts Secure cookies; an explicit local HTTP compatibility override exists and must not be used for public deployment. Read endpoints and JSON export assume synthetic local research data and are not a production confidentiality boundary.

Regression coverage lives in `tests/test_console.py`, the existing offline security suite, frontend unit tests, and real Playwright workflows. See [FRONTEND_ARCHITECTURE.md](FRONTEND_ARCHITECTURE.md).

## Stage 5 extension

Adaptive proposals are strict data constrained by a fixed synthetic template allowlist. Trusted
validation precedes environment creation and is repeated at the evaluator entry. Generated
content cannot configure policies, identities, approvals, destinations, tool definitions or code.
The only paid path is the explicitly authorized, priced CLI provider injection; browser live mode
is still refused. Gateway controls remain mandatory under every benchmark configuration.

Additional identities are sealed before execution and retain ordinary role permissions and ACLs.
Trusted proposal limits fail closed with persisted decisions. Monitors remain capability-free,
consume as-of observations and cannot relax deterministic decisions. Robustness changes only
experimental copies, with exact change records. Ground truth joins predictions afterward, and
adaptive search is restricted to its own partition. Public inherited held-out fixtures cannot
support independent blind-test claims.

Authenticated research endpoints reuse operator sessions, exact local Origin, JSON and CSRF.
Detailed label-bearing research reads require a researcher session; no simulated agent gets HTTP
or database access. Research jobs share one worker, have a bounded queue and never resume after
restart. Accepted controls, rejected POSTs, CAS progress and candidate records are audited. See
ADVERSARIAL_THREAT_MODEL.md for capabilities, assumptions and limitations.
