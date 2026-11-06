# AGENTS.md: instructions for Claude Code, Codex, and other coding assistants

Read `docs/ARCHITECTURE.md` and `docs/THREAT_MODEL.md` before touching `swarmguard/security/`, `swarmguard/tools/`, `swarmguard/agents/workspace.py` or `swarmguard/storage/`. Read `docs/STAGE2_RESEARCH_DESIGN.md` before touching `swarmguard/monitoring/`, `swarmguard/evaluation/` or the Stage 2 detectors.

## Non-negotiable rules

1. **Never bypass the Capability Gateway.** Every side effect goes `AgentGatewayClient.submit` → `CapabilityGateway.submit` → `PolicyEngine` → `MockToolExecutor.execute` (HMAC permit). Do not add another path, and do not give agents the workspace, database, repositories, executor, approval manager or policy.
2. **Never trust agent-reported identities.** Authorization identity comes only from the credential, through `TrustedAgentContext`. A proposal's `agent_id` is only a claim; do not add role or permission fields to proposals.
3. **Never let ethics monitors override deterministic authorization.** Advice may turn ALLOW into ESCALATE and nothing else. It must never relax BLOCK or ESCALATE, create approvals, grant permissions, change policy, or execute anything.
4. **Never permit ordinary agents to grant approvals.** Only `ApproverCredential` holders (the simulated human) can approve or reject. Agent IDs cannot become approvers.
5. **Never execute real email operations in the simulator.** No networking, SMTP or email-API imports in `swarmguard/` (an AST test enforces this). The only network-capable code is the opt-in LLM adapter in `swarmguard_providers/`. Use only reserved example domains and synthetic data.
6. **Always maintain resource versioning.** Every write requires `resource_version`, uses compare-and-set, writes a `resource_versions` snapshot, and emits an event naming the responsible agent. No blind overwrites.
7. **Always log security decisions.** Every submission, including malformed and unauthenticated ones, produces a persisted `DecisionRecord` and events.
8. **Always add regression tests for authorization changes**, covering both the allowed and the blocked case.
9. **Never fabricate evaluation results.** Mock monitors keep `is_mock=True` and the `[MOCK …]` prefix. Demo scenarios are not evidence for RQ1–RQ3. `EvaluationResult.is_research_result` stays `False` until a real experiment runs.
10. **Preserve compatibility with the existing architecture.** Add `ReasonCode` values; never rename them. Keep `EthicsMonitor.evaluate(action, workflow_context)`, `WorkflowContext`, `ActionProposal` and the graph node/edge types stable, or migrate every caller and test.

## More invariants

- Fail closed: any exception during authorization or execution → BLOCK, nothing executed.
- Protected execution re-validates version, content and approval inside one `Database.transaction()` (`BEGIN IMMEDIATE`). Approval consumption is a conditional UPDATE.
- Graph edges must come from explicit references. Chronological edges must have `causal=False`.
- The observability API is GET-only, read-only (`mode=ro`), and never accepts SQL.
- No secrets in code. Stage 2 keys come from the environment (`.env` is gitignored).

## Stage 2 rules (monitoring and evaluation)

- **Monitors are built only from `ObservationBuilder`.** Never give a monitor a database, repository, workspace, gateway or executor handle. Never let it read mutable current rows. Snapshots are as-of `cutoff_seq`.
- **Same rules, different windows.** Local and global monitors must keep sharing `RiskAnalyzer`. A new detector applies to every window; it must never branch on `ctx.window`.
- **No label leakage.** Ground truth (`Scenario.ground_truth()`) is read only after predictions are fixed. Workflow IDs and titles are neutral. Detectors must never inspect scenario IDs, titles or task descriptions to decide anything.
- **Never tune detectors to the benchmark labels and then report the benchmark as evidence.** If a rule changes, bump `RULESET_VERSION`, re-run, and say so in `HACKATHON_WORK.md`.
- **Enforcement mode can only escalate.** `WorkflowGuard` runs after a policy ALLOW, maps alerts to ESCALATE `WORKFLOW_RISK_ESCALATION`, and fails closed. Never let it block on its own, approve, or relax anything.
- **Evidence must be in-context.** `RiskAnalyzer` raises `EvidenceIntegrityError` otherwise; keep it that way, and apply the same validation to future LLM monitors.
- **Results are measured or absent.** Metrics with zero denominators are `None` ("undefined"). Do not hand-edit files under `results/`. The code revision is recorded as-is (`-dirty` included) and never invented.

## Stage 3 rules (LLM monitors)

- **Network code lives only in `swarmguard_providers/`.** `swarmguard/` must never import it or `openai` (AST tests). Entry-point scripts inject providers.
- **LLM monitors are advisory and capability-free.** A monitor holds only an `LLMProvider`. Never give it a gateway, database, workspace, executor, approval manager or policy. Never let an ethics result reach execution except through `DecisionEngine.combine` (ALLOW → ESCALATE only).
- **Trusted/untrusted prompt separation.** System prompts are built from constants in `ethics/prompts.py`. Workflow data goes only into the JSON user message. Never format agent-authored text into a system prompt. Changing any prompt text means bumping `PROMPT_VERSION`.
- **Never fabricate assessments or usage.** Provider failures and invalid outputs are recorded as failures. Unknown usage stays "unavailable". Mock output stays labelled synthetic (`is_synthetic`, `[SYNTHETIC MOCK …]`).
- **No silent mock fallback.** Live configuration errors stop before any request.
- **Pre-register before live runs.** Aggregation, scoring and checkpoint policy are set in `EthicsExperimentConfig` before a run and never tuned on its results. Do not overwrite `results/stage3/*`.
- **The full test suite must pass offline** (the `conftest` socket block). Never add a test that needs a real key.

## Conventions

- Python ≥ 3.11, full type hints, Pydantic v2. Anything agent-supplied uses `extra="forbid"` and `strict=True`.
- New tool checklist:
  1. a params model in `models/actions.py`,
  2. a `ToolSpec` and role permissions in `security/policy_engine.py`,
  3. a function in `tools/mock_tools.py` that calls `ctx.verify(...)` first and is registered in `_TOOL_FUNCTIONS`,
  4. tests.
- SQL lives only in `storage/database.py` and `storage/repositories.py`.
- Use the injected `Clock` (no `datetime.now()` in trusted code) and `Database.next_id` (no `uuid4`) so runs stay reproducible.
- Before finishing: `python -m pytest -v` **and** `python demo.py` (must print `Acceptance check: PASS`) **and** `python demo_stage2.py` (must print `Stage 2 demonstration check: PASS`) **and** `python demo_stage3.py --provider mock` (must print `Stage 3 demonstration check: PASS`).
- Hackathon-period work must be recorded separately from `PRE_HACKATHON_WORK.md`.


## Stage 4.5 — browser control boundary

- Keep `swarmguard.api.create_app` and `main:app` GET-only and read-only. The console is a separate entry point (`console_main:app`, `swarmguard/console/`), with read queries using `mode=ro`.
- Browser mutations require a server-side operator session, exact local Origin, JSON, and a session CSRF token. Never accept an approver identity, trusted agent credential, raw SQL, tool invocation, arbitrary command, or policy change from the browser.
- Operator provisioning is a local hidden-input CLI; persist only a salted password hash. Session cookies are HttpOnly, Secure by default, and SameSite=Strict. No credentials in localStorage or API responses.
- Approval grants must revalidate pending state, workflow/runtime ownership, request expiry, resource version, protected parameters, and the reviewed operation digest before invoking the existing ApproverCredential-bearing service. Historical/interrupted requests are read-only.
- Jobs have strict immutable configurations, bounded worker/queue/request/runtime/step limits, persistent CAS progress/snapshots, explicit cancellation, and no automatic restart recovery. Run one Uvicorn worker only.
- Browser live execution stays disabled until an end-to-end provider/cost path is implemented and tested; explicit confirmation alone must not unlock it.
- Preserve graph causal flags and event IDs/sequences. WebSocket delivery is authenticated; cursor polling recovers authoritative database events. Never infer causal edges from chronology.
- Console mock outputs remain synthetic. Failed/unrun trials stay visible; undefined metrics stay null. Freeze predictions before joining labels. Do not change original result files.
- After console changes, run frontend `npm run lint`, `npm test`, `npm run build`, and isolated Playwright tests, in addition to the existing Python suite and three required demonstrations.

## Stage 5 — adaptive research integrity

- Generated definitions are data only. Validate against the fixed template/partition allowlist before every execution. Never accept policies, credentials, approvals, executable code or new tool definitions from a candidate.
- Adaptive search uses only `adaptive_search`. Never feed held-out labels or feedback to generation or selection. Inherited public held-out fixtures do not constitute an independent blind benchmark.
- Freeze predictions before reading trusted ground truth. Record monitor detection, gateway authorization, actual prohibited outcomes, and legitimate completion separately. Failed assessments are unknown, not safe.
- Robustness transforms modify copied ObservationBuilder contexts only. Persist exact inputs and changes, and preserve mandatory proposal/approval/invariant evidence and cutoff checks.
- New detection rules apply to all windows through RiskAnalyzer. Bump RULESET_VERSION and rerun when changing them. Do not relabel old results or claim improved generalization from tuned fixtures.
- Stage 5 browser jobs share the console execution semaphore and existing authentication/Origin/CSRF rules. Browser live mode stays disabled. Paid CLI runs require explicit opt-in, positive priced budgets and request caps. Never resume research automatically after restart.
- Stage 5 exports are exclusive writes. Preserve CAS progress versions, actors, trajectories, failure records, source fingerprints and manifests. Mock council outputs remain synthetic; unknown tokens stay unavailable.
- Before finishing Stage 5 changes, also run `python demo_stage5.py --provider mock`, `python run_adaptive_evaluation.py --provider mock`, and `python run_benchmark.py --provider mock`, plus the existing backend, frontend and browser checks.

## Stage 6 — framework integrations and regression gates

- Framework adapters add **no** authorization logic and no execution path. Every protected call goes through a gateway-bound framework tool → `SwarmGuardClient` → `IntegrationRuntime` → the existing gateway. Never give a framework agent an executor, simulator, approval manager, `LocalSwarmGuard`, `BaselineManager` authority or an unwrapped protected function.
- Identity comes from host registration of a host-owned object (`IdentityMapping`, object identity). Never authenticate from names, roles, `agent_id` fields or native framework metadata. Native events that cannot be attributed to a registered object are dropped and counted.
- Framework/native events are `FRAMEWORK_REPORTED` observations. Never treat them as execution proof, approval evidence or causal links. Only gateway decisions are `GATEWAY_VERIFIED`.
- Observability-only adapters must declare interception/wrapping/approval capabilities `false` and refuse execution. Never describe hook registration as a security boundary.
- Use public framework APIs only, and record tested versions in the registry and docs. Version families outside `SUPPORTED` stay `UNSUPPORTED`. Optional frameworks must never be required for the core suite.
- `RegressionPolicy.maximum_prohibited_outcomes` is fixed at 0 and the policy version is a literal. Never make the mandatory invariant configurable. Incomplete/incomparable evaluations are INCONCLUSIVE (exit 2), never passing. Changing thresholds or rules means bumping the policy version.
- Only trusted principals designate baselines. Never hand-edit `.ci/swarmguard-baseline.json`; regenerate it with `run_regression.py --create-baseline`.
- Before finishing Stage 6 changes, also run `python demo_stage6.py --framework <each available>`, `python run_framework_evaluation.py --provider mock`, `python run_regression.py --provider mock --baseline .ci/swarmguard-baseline.json`, the `examples/`, and `npx playwright test e2e/stage6.spec.ts` (set `SWARMGUARD_E2E_PYTHON` to an interpreter with the frameworks installed).

## Stage 7 — service separation and production hardening

- The remote path is `agent-runtime → POST /api/v1/capabilities/execute (gateway) → CapabilityGateway → RemoteExecutionBackend → executor`. Never add an agent-reachable route to the executor, a second execution path, or an executor endpoint that accepts anything but a gateway-signed grant bound to an `AUTHORIZED` operation row.
- Rule 5 still holds for `swarmguard/`: no network imports. Network code lives only in `swarmguard_providers/` (LLM), `swarmguard_services/` (internal gateway→executor HTTP channel and uvicorn entry points) and `swarmguard_agent/` (untrusted client; imports nothing from `swarmguard`). None of them may import SMTP/email modules (AST tests).
- Agent identity comes only from a verified EdDSA credential plus the server-side `agent_bindings` row. Never read identity, role, workflows or capabilities from request bodies or token claims beyond `sub`/`rt`. Approver IDs must never be agent IDs.
- Protected operations are at-most-once. Never retry an operation whose state is `EXECUTING` or `UNKNOWN`, never report `UNKNOWN` as "not executed", and only conclude `FAILED` through a successful CAS from `AUTHORIZED`. Recovery never re-executes.
- Fail closed: if mandatory authorization state cannot be read or written, return `AUTHORIZATION_SERVICE_UNAVAILABLE` and execute nothing.
- The audit chain is append-only: no UPDATE/DELETE methods on `AuditChainRepository`, and no service DB role may update or delete audit tables. Do not claim tamper-proofing; it is tamper-evident relative to external checkpoints.
- Console roles: every mutation names its role; no role implies another; the browser never supplies identity, role or approver. Sensitive actions need a reason and explicit confirmation. The hardened control service runs with `inprocess_execution=False`.
- Secrets: files only (Docker secrets / `*_FILE` variables), never in URLs, images, logs, events, metrics or agent containers. The identity issuer private key is host-only.
- Before finishing Stage 7 changes also run `python demo_stage7.py`, `python verify_stage7.py`, and (with Docker) `python deployment/scripts/verify_deployment.py`; run `tests/test_postgres_backend.py` with `SWARMGUARD_TEST_POSTGRES_URL` pointing at a disposable loopback server.
