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
