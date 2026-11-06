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
