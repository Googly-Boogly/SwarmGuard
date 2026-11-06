# AGENTS.md: instructions for Claude Code, Codex, and other coding assistants

Read `docs/ARCHITECTURE.md` and `docs/THREAT_MODEL.md` before touching `swarmguard/security/`, `swarmguard/tools/`, `swarmguard/agents/workspace.py` or `swarmguard/storage/`.

## Non-negotiable rules

1. **Never bypass the Capability Gateway.** Every side effect goes `AgentGatewayClient.submit` → `CapabilityGateway.submit` → `PolicyEngine` → `MockToolExecutor.execute` (HMAC permit). Do not add another path, and do not give agents the workspace, database, repositories, executor, approval manager or policy.
2. **Never trust agent-reported identities.** Authorization identity comes only from the credential, through `TrustedAgentContext`. A proposal's `agent_id` is only a claim; do not add role or permission fields to proposals.
3. **Never let ethics monitors override deterministic authorization.** Advice may turn ALLOW into ESCALATE and nothing else. It must never relax BLOCK or ESCALATE, create approvals, grant permissions, change policy, or execute anything.
4. **Never permit ordinary agents to grant approvals.** Only `ApproverCredential` holders (the simulated human) can approve or reject. Agent IDs cannot become approvers.
5. **Never execute real email operations in the simulator.** No networking, SMTP or email-API imports in `swarmguard/` (an AST test enforces this). Use only reserved example domains and synthetic data.
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

## Conventions

- Python ≥ 3.11, full type hints, Pydantic v2. Anything agent-supplied uses `extra="forbid"` and `strict=True`.
- New tool checklist:
  1. a params model in `models/actions.py`,
  2. a `ToolSpec` and role permissions in `security/policy_engine.py`,
  3. a function in `tools/mock_tools.py` that calls `ctx.verify(...)` first and is registered in `_TOOL_FUNCTIONS`,
  4. tests.
- SQL lives only in `storage/database.py` and `storage/repositories.py`.
- Use the injected `Clock` (no `datetime.now()` in trusted code) and `Database.next_id` (no `uuid4`) so runs stay reproducible.
- Before finishing: `python -m pytest -v` **and** `python demo.py` (must print `Acceptance check: PASS`).
- Hackathon-period work must be recorded separately from `PRE_HACKATHON_WORK.md`.
