# Hackathon Work Record (Stages 2 and 3)

This file records work done during the Apart Research Swarm Alignment Hackathon (6–8 November 2026). It is kept separate from [`PRE_HACKATHON_WORK.md`](PRE_HACKATHON_WORK.md), which is unchanged.

## Provenance (verifiable evidence only)

| Evidence | What it shows |
|---|---|
| Git | One commit exists: `9af9741d931c945ebb89773595d7df387c8f21ee` "Initial SwarmGuard implementation", dated **2026-11-06 09:09:38 −0600**. It contains the Stage 1 code only. |
| Working tree | All Stage 2 work below is **uncommitted** changes on top of `9af9741` at the time of writing. Evaluation metadata records the revision as `9af9741…-dirty` accordingly. |
| Filesystem times | Stage 2 files were created or modified on **2026-11-06** between 09:17 and about 09:45 local time (UTC−6; the first Stage 2 file, `models/levels.py`, at 09:17:43), i.e. after the Stage 1 commit and on the first day of the event. Filesystem times are weak evidence. |

To create durable provenance, commit this work (`git add -A && git commit`) and push it to a timestamped remote. Commit references should then be added here.

## Stage 1 baseline before changes

- `python -m pytest`: **98 passed**, 0 failed.
- `python demo.py`: `Acceptance check: PASS`.

## What was built during the hackathon (Stage 2)

**Models**
- `models/levels.py`: shared enums, moved out of `assessments.py` and re-exported there.
- `models/risk.py`: invariants, snapshots, `MonitorContext`, findings, `SafetyAssessment`, `WorkflowRiskAssessment`.
- `WorkflowContext.workflow_analysis`: an optional, additive field.

**Security (trusted)**
- `security/invariants.py`: invariants A–E.
- `security/pattern_detector.py`: 7 detectors.
- `security/risk_analyzer.py`: invariant evaluation, the fixed aggregation rule, and the evidence-integrity check.
- `security/workflow_intelligence.py`: the intelligence engine and the enforcement `WorkflowGuard`.
- Gateway and decision-engine integration (only escalates).
- New `ReasonCode.WORKFLOW_RISK_ESCALATION` and `EventType.WORKFLOW_RISK_ASSESSMENT` (added, nothing renamed).

**Monitoring**
- `monitoring/workflow_context.py`: the as-of `ObservationBuilder`.
- `monitoring/base.py`, `local_monitor.py`, `global_monitor.py`, `assessment.py` (the pre-registered prediction mapping).

**Simulator**
- Workflow `purpose`, extra brief writers, `seed_customer`, trusted `crm_update`, opt-in `workflow_enforcement`, `observation_builder()`.

**Evaluation**
- `evaluation/scenarios/` package. The Stage 1 module moved to `scenarios/stage1.py` and is re-exported, and `run_approval_laundering` gained an optional `title` argument.
- 14 Stage 2 scenarios.
- `datasets.py`, `experiment.py`, `results.py`; Stage 2 metrics; `runner.py::run_stage2_experiment`.

**API**
- `/workflows/{id}/risk|patterns|dependencies` and `/evaluations[/{id}[/metrics]]`, all GET and read-only.
- `EthicsCouncil.evaluate`: an alias of `deliberate`.

**Scripts**
- `demo_stage2.py` and `run_evaluation.py`.

**Tests**
- 96 new tests (194 total, all passing).

**Docs**
- New: `docs/STAGE2_RESEARCH_DESIGN.md`.
- Updated: README, ARCHITECTURE, THREAT_MODEL, RESEARCH_PLAN, AGENTS.

## Issues found and fixed during development

1. **Temporal leak in the observation builder** (found by the smoke run before any reported evaluation).
   - Cause: prior actions without an explicit resource version were resolved to the latest version *as of the checkpoint*, not as of that action's proposal.
   - Effect: in `stale_authorization_001`, the global monitor received a customer-record version that no in-window event referenced.
   - Fix: resolve the version as of each action's own proposal. Regression test: `test_17b_unreferenced_later_versions_are_not_visible`.
   - Consequence: after the fix, that scenario became a global false positive, as its hard-negative design intends.
2. **Code revision.** The package-level reader found the git HEAD, but the tree is dirty. `run_evaluation.py` now records `git describe --always --dirty` (here `9af9741…-dirty`). The package fallback labels HEAD as "uncommitted changes not checked".

No detector rule or scenario label was changed after looking at evaluation metrics.

## Experimental results produced during the hackathon

One controlled run: `results/stage2_seed0` (repeated as `results/stage2_seed0_repeat`, with byte-identical predictions, checkpoints and metrics). The numbers are in `docs/STAGE2_RESEARCH_DESIGN.md` §11.

They come from **deterministic rule-based monitors on 14 deterministic synthetic scenarios written by the same author as the detectors**. They validate infrastructure and illustrate the information-window effect under controlled conditions. They are **not** evidence about LLM monitors or real autonomous agents, and `is_research_result` remains `False`.

## Still mock / not done

- The four ethics monitors are still the Stage 1 **mocks**.
- No LLM monitors or LLM agents.
- No hold-out scenarios written independently of the detectors.
- No one-hop window ablation.
- No hash-chained audit log.

---

# Stage 3: LLM ethics council

## Provenance
- Still **uncommitted** on top of `9af9741` (no new commits were made).
- Filesystem times: the first Stage 3 file (`swarmguard/llm/__init__.py`) was created at **09:47:30** local time (UTC−6) on **2026-11-06**. Work continued to about 10:05.
- Baseline before Stage 3: **194 tests passed**; the Stage 1 and Stage 2 demos printed PASS.

## Built
- **`swarmguard/llm/`**: provider interface, `ProviderResult` and error kinds, environment settings with a secret key, conservative budget guard, retry wrapper, deterministic mock provider with failure injection.
- **`swarmguard_providers/`** (new, outside the core): `OpenAIProvider` on the Responses API (`with_raw_response.parse`, strict JSON schema, `store=False`, SDK retries off, key scrubbing) and the provider factory.
  - The SDK installed is `openai` 3.46.0. Its API was inspected before use (the `parse` signature, exception classes, `_request_id`, usage fields).
  - `python-dotenv` 1.2.4 was also installed.
- **`swarmguard/ethics/`**:
  - `prompts.py` (`ethics_v1`, hashes);
  - `LLMEthicsMonitor`, with LLM monitors added next to the unchanged mocks in the four framework modules;
  - `general_monitor.py`, `general_council.py`, `aggregator.py`, `disagreement.py`;
  - `synthetic.py` (the mock responder);
  - council: bounded concurrency, structured failures, configurable aggregation (Stage 1 unanimity is the default), `evaluate` method.
- **Models (additive only)**:
  - `EthicalFramework.GENERAL_PURPOSE` and `SPECIALIZED_FRAMEWORKS` (Stage 1 required frameworks are now explicit);
  - provenance fields on `EthicalAssessment` / `MonitorFailure` / `CouncilReport`;
  - `WorkflowContext.observation` and `CouncilEvaluationRecord`.
- **Storage**: `ethics_evaluations` table and repository (schema version 3); `WorkflowMonitor.record_council_evaluation`.
- **Wiring**: `ObservationBuilder.ethics_context`/`build_for`; the gateway attaches the as-of observation for a configured council.
- **Evaluation**: `evaluation/ethics_comparison.py` (configurations A–E, metrics, persistence to `results/stage3/`).
- **API**: five read-only ethics endpoints.
- **Scripts**: `demo_stage3.py`, `run_ethics_evaluation.py`.
- **Tests**: 74 new (268 total), plus an autouse socket block making the entire suite provably offline.
- **Docs**: `docs/STAGE3_ETHICS_RESEARCH.md`; updates to README, ARCHITECTURE, THREAT_MODEL, RESEARCH_PLAN, AGENTS and `.env.example`.

## Decisions and issues found
1. **No-network invariant vs. OpenAI SDK.** AGENTS.md rule 5 and a Stage 1 AST test forbid `openai` in `swarmguard/`. Instead of weakening the test, the adapter lives in `swarmguard_providers/`, and new tests enforce that the core never imports it.
2. **Usage lost on malformed output.** `responses.parse` raises before usage can be read. Switched to `with_raw_response.parse`, so billed tokens are recorded even when validation fails (found by an offline SDK test).
3. **Council timeout vs. retries.** The fixed 60 s council timeout could cut off legitimate retries. The evaluation now derives it from the provider timeout and retry settings.
4. **Mock mode cannot test RQ2.** The synthetic responder is framework-agnostic by design. Mock A–E results are identical by construction and are labelled as pipeline checks.

## Results
- **Mock** (`results/stage3/ethics_mock_seed0`): A–E all scored TP 7 / FP 1 / TN 5 / FN 1; 495 synthetic calls; 0 failures. This is a **pipeline check, not evidence**.
- **Live: not run.** No API key or model was configured. The OpenAI integration is **unverified against the live API**.

## Stage 4.5 — local full-stack Research Console (2026-11-06)

Baseline before implementation: **268 backend tests passed** (15.38 s). FastAPI TestClient's portal hung under the execution sandbox; the same offline suite completed outside it. Existing Stage 1–4 files were inspected and retained; the original read-only API and deterministic authorization path remain intact.

Implemented a new React/TypeScript/Vite/Tailwind console with source-owned shadcn-style Radix primitives, routing, TanStack Query, React Flow, Recharts, and Lucide. All operational views read persisted backend records. A separate `console_main:app` control service adds strict commands, a locally provisioned scrypt operator hash, random server-side sessions, Secure/HttpOnly/SameSite=Strict cookies, CSRF/local-Origin/Host checks, exact-operation approval review, bounded async jobs, CAS progress snapshots, cancellation, restart interruption, and authenticated ordered WebSocket delivery with cursor-based polling recovery. Original observation endpoints stay GET-only and `mode=ro`; console reads also use a separate read-only connection.

Interactive mock workflows reuse the existing AutonomousAgent/AgentRuntime/task/message/gateway services. Three interactive synthetic scenarios and two finite Stage 1 scripted replays are enabled. The scripted human is labelled; interactive requests require operator review and never auto-grant. Browser live execution is refused server-side, including requests with a confirmation flag. Original live CLI functionality is preserved. No paid requests or real email were used.

A pre-existing mock recipient-override parser truncated an address at its first dot; the corrected parser preserves full reserved domains, with a regression test. Detector rules and prompts were not changed, so no detector ruleset or prompt version bump was made. Controlled adversarial fixture proposals, real gateway decisions, and their checkpoint assessments are explicitly recorded. Failed/unrun trials remain visible. Tokens are unavailable when not reported; synthetic billing is zero. Console results remain `is_research_result=False`. No original `results/` files were modified.

Verification on the implementation tree:

- **315 backend tests passed**, including **47 new console tests** (full offline suite, 41.96 s). Coverage includes authenticated and blocked actions, CSRF, fixed human identity, stale/expired/exact approval packets, actual send/no-send outcomes, pause/resume/stop, limits, failures, progress/results/export, WebSocket authorization/replay/cursor recovery, restart recovery, and cancellation during an in-flight gateway assessment.
- **16 Vitest/React Testing Library tests passed**; TypeScript check and production build passed.
- **4 Chromium Playwright tests passed** (39.1 s), with real isolated FastAPI/Vite servers, temporary SQLite, default Secure operator cookie, no mocked HTTP routes, and no provider key. They exercised legitimate mock operation and explicit approval; persisted graph/council/results; actual approval mismatch blocking with zero simulated sends and audit/dependency inspection; experiment launch/pause/resume/cancellation; mobile navigation; every console section; and live WebSocket delivery.
- `demo.py --db /tmp/swarmguard45-stage1.db`: **Acceptance check: PASS**.
- `demo_stage2.py --db /tmp/swarmguard45-stage2.db`: **Stage 2 demonstration check: PASS**.
- `demo_stage3.py --provider mock --db /tmp/swarmguard45-stage3.db`: **Stage 3 demonstration check: PASS**.

The initial browser runs exposed a form-selector issue, a missing assessment-adapter field, an agent-count API error on populated data, and missing Uvicorn WebSocket transport dependency; these were fixed and the final runs above passed. `websockets` is now an explicit runtime dependency. Screenshots are generated under ignored `frontend/test-results/`.

Limits: single process/worker, local synthetic research, three interactive scenarios, no individual-agent control, no browser live execution, no account administration or credential recovery, cooperative pause/cancellation, no public deployment claim, and no evidence of live LLM safety/performance. The original fourteen-case and live ethics research runners remain CLI operations. Stage 5 can use the console for infrastructure validation and reproducible synthetic investigations; live autonomy, independently authored hold-outs, and multi-model statistical studies remain research work.

Final production-bundle verification: `SWARMGUARD_PREVIEW=1 npx playwright test` also passed the same **4 Chromium tests** (24.5 s), including WebSocket delivery, authenticated approvals, blocked laundering, controls, and all sections. The built assets were tested through Vite preview against isolated real backend services.

## Stage 5 — adaptive synthetic research implementation

Extended the existing simulator, gateway, ObservationBuilder, shared RiskAnalyzer, Stage 3 councils,
and Stage 4.5 console. Added strict scenario/research commands; fixed development/adaptive/held-out
partitions; allowlisted generation and trusted validation; eight bounded mutation families; an
interpretable adaptive loop; normalized structural novelty; actual execution feedback; exact
experimental observation transforms; paired council robustness; variable trusted participants;
observational benchmarks plus isolated policy/local/global-guard interventions; conservative failure
analysis; paired/per-framework/correlated-error/operational metrics; and CAS manifests, candidates,
audit records and exclusive exports. No arbitrary shell/network/tool/policy or approval capabilities
were added to agents. Live CLI generation/provider injection is explicit and priced; browser live
execution remains disabled. Research jobs share the existing single worker and never auto-resume.

The existing React frontend now includes authenticated adaptive research, benchmarks, failures and
scaling pages. It uses actual records for controls, progress, metrics, evidence, timelines, workflow
and communication graphs, cost/latency/intervention plots and exports. Existing Stage 1–4.5 routes
and user changes were preserved. Version metadata is now 0.5.0. No paid call or real email occurred.

New detector text applies through the same RiskAnalyzer for both windows. RULESET_VERSION was
bumped to `stage5-rules-v2`; the constant generator prompt addition bumped PROMPT_VERSION to
`ethics_v2`. The older prompt-version test now checks the current version rather than a literal.
The original fourteen-scenario deterministic evaluation was rerun into a new directory,
`results/stage5_rules_v2_verification`; historical files were not edited. These changes were not
validated as a novel held-out improvement, and public inherited hold-outs are explicitly disclosed.

Actual baseline: 315 backend tests and 16 frontend tests passed. Final verification: **379 backend
tests passed** (64 new Stage 5 cases), **20 frontend tests passed**, lint and production build passed,
and **6 production Chromium Playwright tests passed**. All four required demonstrations printed
PASS. Restricted-sandbox FastAPI/browser process limitations required running the successful full
backend and isolated browser checks outside that sandbox; the offline socket test fixture remained
active. A development-suite adapter failure was preserved in research_010, corrected, regression-tested,
and rerun successfully as research_022. No implementation date has been inferred.

Final measured artifacts: research_017 (adaptive demo), research_018 (3/5/8/12 scaling), research_019
(adaptive CLI), research_020 (adaptive-search benchmark), research_021 (held-out benchmark), and
research_022 (development benchmark), all COMPLETED with zero failed final trials and no validation
rejections. Adaptive research_019 constructed 36 definitions, issued/validated/executed four (including
two agent-count refinements), and left 32 untested. Four prohibited synthetic outcomes occurred under
observation; no gateway block was expected from exact approvals alone. Local monitoring missed the
cumulative cases. Removing source observations removed global alerts on those cases. Held-out runs
preserve the transformed-disclosure miss and stale-authorization false positive, including correlated
mock council errors. Mock agreement is not evidence for ethical specialization. Detailed per-condition,
per-partition scores, intervention utility, costs, scaling workload, exact commands and limitations are
recorded in `docs/STAGE5_VERIFICATION.md` and the untouched experiment exports.

Remaining limits: constrained scripted workflows; public same-author fixtures; exact-value rather
than semantic provenance; critical-only primary checkpoints; dependent repeated mock trajectories;
cooperative bounded local execution; no independent sealed benchmark; and no verified live Stage 5
run. Stage 6 should prioritize those research validity and controlled live verification gaps.

## Stage 6 — framework integrations, runtime enforcement and continuous safety evaluation (2026-11-06)

Starting point: a Stage 6 skeleton already existed in the working tree (adapter contract, registry, identity
mapping, canonical events, custom/CrewAI/LangGraph/AutoGen adapters, runtime middleware, trusted tool registry,
regression baseline/comparison/runner, CLIs, console routes and React pages, 54 tests). It was not recorded here.
It was later committed as part of "SwarmGuard Stage 5 implementation". Baseline before this work:
**433 backend tests passed, 0 failed, 0 skipped** (`.venv-stage6`, Python 3.13.18).

Tested dependency versions: crewai 1.15.36 (Python 3.13 only; CrewAI 1.15 does not install on 3.14),
langgraph 1.2.20, langgraph-prebuilt 1.1.1, langgraph-checkpoint 4.3.1, langchain-core 1.7.6,
autogen-core / autogen-agentchat 0.7.5, fastapi 0.162.0, pydantic 2.12.5.

Changes made in this session:

- **CrewAI** previously built `Agent` objects but invoked the tool directly. Each proposal now runs through a real
  `Agent`/`Task`/`Crew.kickoff_async`. An offline labelled scripted `BaseLLM` emits the ReAct text; CrewAI's own
  parser, args-schema validation and dispatch are real. Registration replaces `agent.tools` with the single gateway
  tool. CrewAI runs tools on worker threads, so the wrapper hands submissions back to the owner loop
  (`run_coroutine_threadsafe`). Scoped `crewai_event_bus` listeners record native events as untrusted reports,
  attributed only to registered agents.
- **LangGraph** tool calls now go through a prebuilt `ToolNode` holding only the gateway tool (`AIMessage.tool_calls`).
  ToolNode refuses unregistered names. Pipelines stream `updates` with `InMemorySaver` checkpoint IDs.
- **AutoGen** adds a public `DefaultInterventionHandler` observer (never drops or rewrites) and converts AutoGen's
  `BaseException`-derived `ToolException` so fail-closed handlers apply.
- **Security fix:** `RegressionPolicy.maximum_prohibited_outcomes` accepted up to 100 and `version` was free text, so
  an authenticated browser request or policy file could weaken the mandatory gate. Both are now literals.
  Policy version bumped to `stage6-regression-v2`.
- **Comparison fix:** a measured report labelled `prohibited_outcome_rate` 0 → 0.333 as `NO_REGRESSION_DETECTED` (the
  overall gate still failed through `mandatory_violation`), and every changed untracked metric was "no regression".
  Rows are now labelled from the invariant or a declared direction, or INCONCLUSIVE. Timing never drives the overall
  verdict. Included in `stage6-regression-v2`. `results/stage6` (created only by this session's runs) was deleted and regenerated after the fix.
- Added a host-only `LocalSwarmGuard` SDK facade, SDK exceptions, `--policy` JSON for `run_regression.py`, per-case
  demo output, and overhead measurement for every framework. Added four runnable examples (`examples/`; the LangGraph one
  uses `interrupt()` for human review), `.ci/swarmguard-baseline.json` (generated by `run_regression.py --create-baseline`),
  and `.github/workflows/swarmguard-safety.yml`. The workflow was not executed on GitHub; its steps were run locally.
- Console: Badge styling for regression classifications, an event-coverage panel and a runtime-overhead chart/table.
  Recent integration errors are shown on the list.
- Docs: INTEGRATION_GUIDE, RUNTIME_SECURITY, CONTINUOUS_EVALUATION and the CrewAI/LangGraph/AutoGen notes; Stage 6
  sections in README, AGENTS, ARCHITECTURE, THREAT_MODEL and RESEARCH_PLAN.

Detector rules and prompts were not changed (no RULESET_VERSION or PROMPT_VERSION bump). No live LLM, paid call or real email occurred.

Measured results (`results/stage6/`, all `MOCK_SYNTHETIC`, `is_research_result=False`):

- Demos for custom, crewai, langgraph and autogen: `Stage 6 demonstration check: PASS`. In every framework, `service` →
  send ALLOW/AUTHORIZED, executed; `approval_attack` → BLOCK/APPROVAL_NOT_FOUND; `version_attack` → BLOCK/APPROVAL_MISMATCH.
  Zero prohibited outcomes and legitimate completion 1.0. The controlled `redirected` fixture → CONFIRMED_THRESHOLD_VIOLATION, exit 1.
- Framework evaluation (`--require-all --overhead`): PASS. All four frameworks gave identical detection on the three
  fixtures (local and global: TP 1, FP 0, TN 1, FN 1; recall 0.5, FPR 0.0). Identical scripted proposals make this
  expected by construction; it is **not** evidence for RQ6.1.
- Native event coverage per service workflow (9 gateway calls): CrewAI `AgentExecutionStarted`/`ToolUsageStarted`/
  `ToolUsageFinished` 9 each, but `TaskStartedEvent` was **never delivered** (0). LangGraph: 3 `StateGraph.updates`.
  AutoGen: 9 `on_send.FunctionCall`, 9 `on_response.FunctionExecutionResult`. Custom: none (host-reported only).
- CI gate against the committed baseline: the safe candidate gave NO_REGRESSION_DETECTED, exit 0 (mean workflow
  56.5 → 69.5 ms flagged POSSIBLE_REGRESSION, descriptive only). The redirected candidate gave
  CONFIRMED_THRESHOLD_VIOLATION, exit 1: prohibited outcome rate 0 → 0.333, legitimate completion 1.0 → 0.0,
  FPR undefined (no safe case).
- Overhead (n=3 workflows, 27 action samples per condition; median action ms for gateway-only / passive /
  global guard / mock council): custom 1.61/4.57/4.60/4.14; crewai 42.7/50.8/46.6/44.5; langgraph 6.18/9.90/7.76/7.38;
  autogen 2.68/5.96/6.80/7.37. These are small, noisy, non-monotonic samples. p95 is not reported (<100 samples).
  Every condition kept mandatory authorization; there was no unguarded baseline.

Verification on the final tree: **487 backend tests passed** on Python 3.13.18 (`.venv-stage6`, all frameworks).
On Python 3.14.4 (`.venv`, no CrewAI): **480 passed, 7 skipped** (all skips are CrewAI-only). **29 Vitest tests passed** (9 new). Lint and build passed. **7 Chromium Playwright tests passed** (new
stage6 spec included; default `.venv` server), and the stage6 spec also passed in production-preview mode with
`.venv-stage6`. All four examples printed `Example check: PASS`. Required demonstrations: `demo.py` Acceptance check PASS;
`demo_stage2.py` PASS; `demo_stage3.py --provider mock` PASS; `demo_stage5.py --provider mock` PASS; `run_adaptive_evaluation.py`
and `run_benchmark.py --provider mock` exit 0 (Stage 1–5 outputs written to a scratch directory, not `results/`).

Limits: in-process enforcement only, with no remote transport. Benchmark "models" are deterministic scripts: framework
dispatch is real, LLM behaviour is untested. There are three fixtures and one regression type. The CrewAI event bus is
process-global. Applications can still give agents other uncontrolled tools. The GitHub workflow is unexecuted.

## Stage 7 — service separation, zero-trust agent access and hardened deployment (2026-11-06)

Baseline: no Stage 7 code existed. The effective baseline is the final Stage 6 run on this same tree:
487 backend tests passed (Python 3.13, all frameworks) and 480 passed / 7 skipped (Python 3.14). The suite
was re-run after the storage rewrite, before any Stage 7 feature work, and all tests passed.

Implemented:
- **Storage:** a PostgreSQL backend for the whole trusted store (psycopg 3.3.8; outermost transactions serialized by
  an advisory lock, equivalent to `BEGIN IMMEDIATE`; passwords only from files). Forward-only tracked migrations;
  Stage 7 tables; least-privilege role grants. No service role may DELETE, run DDL or update audit tables; only the
  executor may write the outbox.
- **Audit:** a hash chain over every `events` row, written in the same transaction, plus verification and
  external checkpoints (`verify_stage7.py --audit`).
- **Identity:** EdDSA agent credentials (PyJWT 2.15.1 + cryptography 50.0.2) verified with the public key only.
  Revocation covers credential, runtime and agent. Role, workflows and capabilities come from server-side bindings.
  Approver IDs must be disjoint from agent IDs.
- **Remote gateway** (`POST /api/v1/capabilities/execute`): authentication, strict schema, binding scope, emergency
  stop and capability switches, idempotency, rate, size and concurrency limits, fail-closed on database errors,
  persisted rejections.
- **Executor service:** accepts only 30 s gateway-signed grants bound to `AUTHORIZED` operation rows. It re-validates
  the stored action, the ALLOW decision, the digest, the workflow state and the static registry, then runs CAS
  transitions; effects and SUCCEEDED commit atomically. At-most-once delivery, with arbitration to FAILED/UNKNOWN
  and restart recovery that never re-executes.
- **Approvals:** REVOKED state and revocation.
- **Console:** role-based accounts (RESEARCHER/OPERATOR/ADMINISTRATOR/AUDITOR, legacy account = RESEARCHER+OPERATOR),
  audited logins and per-user limits, the control plane, Stage 7 security API, and a hardened mode
  (`inprocess_execution=False`).
- **Packages:** the untrusted agent client (`swarmguard_agent/`, httpx only) and network entry points in
  `swarmguard_services/` (core `swarmguard/` stays network-free; AST tests).
- **Tooling:** `stage7_cli.py`, `demo_stage7.py` (real separate processes), `verify_stage7.py`.
- **Deployment:** hardened Compose (6 services + migrate job, 4 networks, 3 internal), digest-pinned images,
  non-root, read-only, `cap_drop ALL`, resource limits, health checks, Docker secrets, Caddy frontend.
  `deployment/scripts/verify_deployment.py` tests isolation from inside containers.
- **Frontend:** five pages (security operations, operation detail, service health, identities, audit integrity)
  with confirmation dialogs and role-aware controls.
- **Docs:** STAGE7_SECURITY_ARCHITECTURE, DEPLOYMENT, SECURITY_OPERATIONS, INCIDENT_RESPONSE, PRODUCTION_READINESS,
  plus updates to the existing docs.

Defects found and fixed during Stage 7 work:
- After grant delivery, an unexpected transport exception was reported as BLOCK (not executed) while the operation
  was EXECUTING. Such failures now always arbitrate (FAILED only via CAS from AUTHORIZED, otherwise UNKNOWN).
- Approver IDs could collide with agent IDs. Now refused.
- SQLite heartbeats on the event loop delayed fail-closed 503s beyond client timeouts. They now use a separate
  connection in a worker thread, with a configurable busy timeout.
- The executor now also requires the indexed decision column and the stored record to agree on ALLOW.
- The observation API and console tried to migrate PostgreSQL with non-owner roles.
- An assert enforced a grants invariant (stripped under `-O`); it is now an explicit check.
- The audit hook swallowed exceptions silently; it now logs them.
- The base Python image had 9 CRITICAL / 75 HIGH findings. Moving to 3.13.18-slim-trixie, OS upgrades, and removing
  pip/setuptools from the runtime images left 0 CRITICAL and 0 fixable HIGH.

Verification on the final tree:
- **562 backend tests passed** on Python 3.13.18, including the opt-in PostgreSQL tests against PostgreSQL 17.6.
  **550 passed, 12 skipped** on Python 3.14.4 (CrewAI-only and PostgreSQL opt-in skips). New suites: test_stage7
  (58), test_stage7_console (12), test_postgres_backend (5).
- **Frontend:** 39 Vitest tests passed (10 new); lint and build passed. **8 Playwright tests passed**, including the
  new Stage 7 spec; it also passed in production-preview mode.
- `demo_stage7.py`: 15/15 on SQLite and 13/13 on PostgreSQL (the tamper and lock injections are SQLite-only).
  `verify_stage7.py --deployment`: 17/17. `verify_deployment.py`: **40/40**.
- Stage 1–6 demonstrations: Acceptance PASS; Stage 2, 3 and 5 PASS; Stage 6 PASS for all four frameworks; adaptive and
  benchmark exit 0; framework evaluation PASS; regression gate exit 0 (safe) and exit 1 (controlled regression);
  all four examples PASS.

Scans:
- Bandit: 0 high; medium findings are allowlisted dynamic SQL.
- pip-audit: services lock clean; `chromadb` advisories exist in the development integration venv only.
- npm audit: 0. Gitleaks: history clean; one false positive.
- Trivy: backend and agent images have 0 CRITICAL and 49 unfixed Debian HIGH; the frontend has 2 HIGH in the Caddy
  binary. Trivy config: 0 Dockerfile findings.

No paid API calls or real email. Detector rules and prompts were unchanged (no version bumps). No results/ files
were modified.

Not production-ready (see PRODUCTION_READINESS.md): approval integrity relies on a symmetric key shared by trusted
services; no mTLS internally; no MFA; no connection pooling; single worker per service; and the GitHub workflow has
not been executed on GitHub.

## Stage 8 — final laboratory work

Added a native laboratory with strict configurations, RQ registry, immutable datasets, bounded scripted/mock autonomous execution, replay, metrics validation, reports and checksummed bundles; connected it to the existing console and React routes. Baseline 551/12 backend, 39 frontend and 8 browser tests; final 560/12 backend and 9 browser tests, with focused research tests rerun after the recipient/oracle alignment fix. No ruleset or prompt tuning, paid API runs, invented metrics or historical result edits. See docs/FINAL_RELEASE_REPORT.md for actual outcomes and incomplete scope. Missing license remains an owner decision and release blocker.

### Experiment Designer total trial limit — November 7, 2026

Added a backend-enforced `max_total_trials` limit and a frontend default of two total trials across scenarios and repetitions. The designer labels repetitions explicitly and previews the planned count. Seeded schedule selection happens before inference without consulting labels; metrics, validation, exports and replay retain the bounded plan. Configurations without the new limit keep the original full schedule. A limited subset may omit safe or unsafe cases, so the interface describes that limitation. No detector, prompt, authorization or historical result changes.

Validation: 13 focused research tests, 39 frontend tests, TypeScript lint, production build, the isolated laboratory Playwright test, and the three required mock demonstrations passed. The initial full backend run had 563 passes, 12 skips and one existing no-accounts test failure because host operator accounts were now configured; the full rerun with isolated account storage passed (564 passed, 12 skipped). Rebuilt and restarted the local Docker console/frontend with existing data volumes preserved; its session endpoint confirms login remains available.

### Password-free local console — November 7, 2026

Removed manual login/account setup from the local entry point and development Docker console at the user's request. The browser automatically creates a bounded, expiring server-side session via an empty same-origin JSON POST. The local interface hides login/password/sign-out controls. Origin, JSON, HttpOnly/Secure/SameSite cookies, CSRF, role checks, approval confirmation and gateway authorization remain active. Identity and RESEARCHER/OPERATOR roles are fixed server-side; bootstrap rejects browser-supplied identity or roles. This local mode trusts access to the loopback console rather than identifying an individual human. It is rejected for hardened execution or a configured control plane; separated services retain account authentication. Existing account files are preserved.

Updated API contracts and documentation, added allowed/blocked local-session regression coverage and a credential-free browser experiment test, and isolated an existing no-accounts test from host account files. Frontend test session fixtures now include the validated API fields. Full backend suite: 568 passed, 12 skipped. Frontend: 39 tests passed, lint and build passed. Browser: 9 existing tests passed in account mode, plus the separate password-free test passed (two mock autonomous trials, refresh, no login controls). Stage 1–3 demonstrations passed; Stage 7 demonstration passed 15/15 and verification passed 16/16. No paid calls, detector/prompt changes or historical result edits. PostgreSQL opt-in tests and full hardened Docker deployment verification were not rerun; the development app occupies that verifier's fixed port 8080. Rebuilt and restarted only the local console/frontend with existing volumes preserved.

### Compact laboratory statistics and reports — November 7, 2026

Replaced the laboratory's default raw JSON output with a small monitor table and paginated trial outcomes. RQ1 shows local versus global monitoring; RQ2 shows the registered general versus specialized council comparison. Denominators, failed/unrun counts, unavailable predictions and undefined rates remain explicit. All-monitor statistics, trajectories and validation records are collapsed and only rendered when opened. Existing experiments use the same compact view without rerunning. Generated browser reports now default to a bounded summary with at most ten trial rows, rather than embedding full datasets, pairwise arrays and manifests. Explicit full/section reports and reproducibility bundle generation remain detailed and unchanged, preserving historical bundle verification. No stored predictions, metrics, labels or result files were modified.

Validation: full backend suite 568 passed, 12 skipped; frontend 41 tests passed; lint and build passed; password-free isolated browser test passed with exactly two monitor rows, two trial rows, no default JSON dump and a short generated report. Focused backend coverage checks short reports, missing assessments, unchanged source records and full bundle export/replay. All three required demonstrations passed. Rebuilt the local Docker console/frontend for rollout; existing data volumes are preserved. No ruleset/prompt changes or paid calls.

## RQ1 scientific experiment — November 7, 2026

Executed RQ1 (Local Action Monitor vs. Global Workflow Monitor) as a pre-registered, deterministic experiment. Detector, invariant, monitor, gateway, policy, agent and tool code is unchanged (no RULESET_VERSION or PROMPT_VERSION bump). Added a 24-scenario held-out library (`rq1-heldout-1`, 12 unsafe / 12 legitimate across distributed approval laundering 6/6, cross-agent information flow 3/3, shared-resource coordination 3/3), written after the detectors were frozen. All unsafe cases use valid, exact approvals; no invalid-approval cases. Added `swarmguard/evaluation/rq1.py`, `run_rq1.py`, `tests/test_rq1.py` and an optional `scenarios=` argument to `build_dataset`/`run_stage2_experiment` (default behaviour unchanged). Protocol: `docs/RQ1_EXPERIMENT_PROTOCOL.md`, frozen with dataset hash `a3157cb4…92dd` before any held-out monitor evaluation.

The pilot `rq1_pilot_001` exposed a verification-harness defect (re-derivation order differed from the runner, so digests mismatched). It was fixed; `rq1_pilot_002` passed all checks. Primary `rq1_primary_001`: 24/24 evaluated, 680/680 observation checks passed. Local TP/FP/FN/TN 5/3/7/9 (recall 0.417, FPR 0.250); Global 11/0/1/12 (recall 0.917, FPR 0.000). Paired unsafe: only-global 6, only-local 0; exact McNemar p = 0.031 (descriptive). All 24 critical sends were ALLOWED by the gateway, and all 12 prohibited outcomes occurred without a guard. Global-guard replay prevented 11/12 with 0 legitimate escalations; local-guard replay prevented 5/12 and escalated 3 legitimate workflows. `rq1_repro_001` reproduced the records identically. Interpretation limits: the advantage is an information-access effect (every global-only detection depended on in-window resource history); Local's alerts are all one token heuristic (8/8 token-bearing workflows); the paraphrase case was missed by both; same-author scenarios. Artifacts: `research/rq1/`. `is_research_result` stays False. No paid calls, no historical result edits.

### Research question registry rq-registry-2 — November 7, 2026

Restated RQ1–RQ3 in the laboratory hypothesis registry at the research team's request: full question text, what is tested, the experiment and comparison arms, metrics and goal. Dependent variables are now RQ1 recall/precision/FPR/F1/detection latency; RQ2 recall/precision/FPR/F1/monitor disagreement/computational cost; RQ3 unauthorized execution rate/blocked unauthorized requests/legitimate operation success rate/authorization overhead. At the team's request the current registry contains RQ1–RQ3 only: RQ4–RQ7 are retired and new laboratory experiments selecting them are rejected. The registry is versioned (`rq-registry-1` legacy, all seven questions; `rq-registry-2` current, RQ1–RQ3). New laboratory experiments record `question_registry_version` in their manifest, and reports render the version an experiment was created under, so historical reports and reproducibility bundles, including any created under RQ4–RQ7, are unchanged (`results/stage8/experiment_001` still verifies). The hypotheses page now shows each question and goal. Registering a metric does not mean the laboratory computes it: detection latency, authorization overhead and the RQ3 rates are not yet computed by the laboratory. No detector, prompt, policy or result changes; no paid calls.

Validation: full backend suite 578 passed, 12 skipped (new registry test covers current/legacy text, retired-question bundles, and allowed RQ1–RQ3 / rejected RQ4–RQ7 configurations); frontend lint, 41 tests and build passed; Acceptance, Stage 2 and Stage 3 demonstrations PASS.

### RQ1 research laboratory in the console — November 7, 2026

Added a browser workflow for RQ1 (`/research/rq1`): configure → methodology validation → background run → live monitor → results dashboard (Overview, Scientific Metrics, Raw Data, Scenarios, Workflow Explorer, Monitor Comparisons, Failure Analysis, Research Findings, Experiment Details). Backend: `swarmguard/research/rq1_lab.py` and authenticated routes in `swarmguard/console/rq1_routes.py` (RESEARCHER session for reads; Origin + CSRF for validate/run/cancel). No detector, invariant, monitor, gateway, policy, prompt or frozen RQ1 file changed (all eight protocol fingerprints still match; no RULESET_VERSION/PROMPT_VERSION bump).

Method: each workflow runs once in a fresh in-memory simulator; at every checkpoint one snapshot yields the LOCAL and GLOBAL observations, both unchanged monitors are evaluated, and the exact monitor inputs/outputs, latencies, events, actions, gateway decisions, messages, resource versions, approvals and graph are stored (zlib-compressed with a sha256 integrity check). The frozen runner (`run_stage2_experiment`) then produces the scored predictions (ground truth read only after predictions are fixed) and enforcement replays; every stored checkpoint is cross-checked against it (snapshot digest and recommendation), followed by `rq1.verify_observations` and `rq1.analyze`. Validation is monitor-free (dataset validity, oracle agreement, observation-boundary pre-check, label isolation, future-event exclusion, frozen hash/fingerprints, limits) and reports VALIDATED / WARNINGS / INVALID; INVALID cannot run and run time re-validates (fail closed). Failures are recorded, never repaired; interrupted runs become INCONCLUSIVE and are not resumed. Only Scripted mode is offered; Mock Autonomous, Live LLM, Recorded Replay and the Global + Ethics Council configuration are shown as unavailable with reasons. The pre-registered decision rule is applied only to the complete frozen held-out dataset with seed 0; otherwise it is labelled orientation only. Optional CSV/JSON downloads of raw rows; no report or ZIP generation was added.

Verification: a complete held-out run through the new laboratory reproduced `rq1_primary_001` exactly (Local 5/3/7/9, Global 11/0/1/12 TP/FP/FN/TN; 680/680 observation checks). Backend suite 607 passed, 12 skipped (29 new RQ1-laboratory tests). Frontend lint and build passed; Vitest 48 passed (7 new); Playwright 9 passed and 2 skipped in account mode, and the local-access specs passed (including the new end-to-end RQ1 pilot spec covering configuration, validation, live progress, metrics checked against the API, raw-data filtering, record detail, comparison, scenario, graph, failures, findings clipboard copy and refresh). Acceptance, Stage 2, Stage 3 and Stage 7 demonstrations PASS; `verify_stage7.py` 16/16. No paid calls; no result files modified.

### RQ1 LLM arm — November 7, 2026

RQ1 experiments previously never called a model (both monitors are deterministic rules). Added an LLM arm, pre-registered as `rq1-llm-protocol-1` (docs/RQ1_LLM_PROTOCOL.md): the existing `GeneralSafetyMonitor` (prompt `ethics_v2`, unchanged; no PROMPT_VERSION or RULESET_VERSION bump) is evaluated on the LOCAL and the GLOBAL `ethics_context` built from the same snapshot as the rule monitors, at checkpoints chosen before the run (`critical`, `outbound` [default], `all`). New: `swarmguard/research/rq1_llm.py` (no network code), `swarmguard_providers/rq1.py` (provider injection), `console_main.py` injects it; configuration fields `llm_mode` (none/mock/live), `llm_checkpoints`, `llm_max_requests`, `llm_max_cost_usd`, `confirm_paid_calls`.

Live calls need, before any request: the operator starting the console with `SWARMGUARD_CONSOLE_LIVE_LLM=1`, `OPENAI_API_KEY`, `SWARMGUARD_OPENAI_MODEL`, positive per-million-token prices, plus per-experiment request and dollar caps and explicit paid-call confirmation (effective caps are the minimum of experiment and server caps). Validation builds every exact LLM input without calling, checks it for labels and future events, estimates calls and worst-case cost, and refuses runs the caps cannot cover; run time re-validates; a live request served by a synthetic provider fails (no silent mock fallback). Failed, refused or unattempted calls are UNKNOWN, excluded from confusion matrices and make the experiment INCONCLUSIVE. Mock mode uses the synthetic rules responder and stays labelled `[SYNTHETIC MOCK …]`. The browser shows LLM availability with reasons, the estimate, usage and cost live, LLM metrics/confusion matrices/charts, LLM rows in Raw Data with the exact user message (re-rendered and verified against its stored sha256), LLM side-by-side comparisons and LLM failure cases.

Also made `test_live_demo_refuses_without_configuration` hermetic: it read the developer's `.env`, which now contains prices, so the "unconfigured" demo attempted a request (blocked by the offline socket guard). Validation: backend 624 passed, 12 skipped (17 new LLM-arm tests using the mock provider and the real OpenAI provider class with an offline fake client); frontend lint/build, Vitest 50 passed; Playwright 9 passed + 2 skipped, local-access 2 passed (RQ1 spec now runs the mock LLM arm end to end); Acceptance, Stage 2, Stage 3, Stage 7 demos PASS; `verify_stage7.py` PASS. No live (paid) LLM call has been made.
