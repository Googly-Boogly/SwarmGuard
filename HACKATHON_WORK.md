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
