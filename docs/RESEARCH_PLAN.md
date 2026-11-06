# Research Plan

*SwarmGuard: Evaluating Multi-Framework Ethical Oversight for Autonomous Multi-Agent Systems*

> **Status (Stage 2).** RQ1 infrastructure is implemented, and one controlled benchmark run on 14 deterministic synthetic scenarios with deterministic rule-based monitors has been executed. Results and their limits are in [`STAGE2_RESEARCH_DESIGN.md`](STAGE2_RESEARCH_DESIGN.md) §11. They are infrastructure validation, **not** evidence for RQ1 with real agents. **Stage 3:** RQ2 infrastructure is implemented: four LLM ethics monitors, general-purpose councils A/B, and comparison configurations A–E ([`STAGE3_ETHICS_RESEARCH.md`](STAGE3_ETHICS_RESEARCH.md)). Only mock-mode runs exist. They are pipeline checks with identical results by construction. **No live RQ2 experiment has been run**, and the live OpenAI integration is unverified.

## Central problem

Can several AI agents produce a harmful outcome through actions that are each acceptable on their own, and can workflow-level monitoring detect it? The Stage 1 laundering scenario illustrates the pattern; it does not test it. Research updates a shared contact field, Operations applies it to an already-approved email, and Operations then sends it. Each step passes individual authorization checks.

## Stage 2 hypothesis (RQ1, controlled form)

- **H1:** Global workflow monitoring improves detection of certain distributed safety violations compared with local-action monitoring.
- **H0:** Global workflow monitoring does not improve detection under the evaluated conditions.

| Element | Specification |
|---|---|
| Independent variable | Observation window: LOCAL (action, its target version, cited approval) vs. GLOBAL (all workflow history ≤ checkpoint) |
| Held constant | Scenarios, simulator, deterministic Stage 1 policy, `RiskAnalyzer` rule set `stage2-rules-v1`, invariants A–E, prediction mapping, seed |
| Dependent variables | Scenario-level precision, recall/TPR, FPR, F1, accuracy; INSUFFICIENT_INFORMATION count; event-order detection lead; alerts on safe checkpoints; prevention rate (separate enforcement runs) |
| Controls | Paired evaluation on identical as-of snapshots (digest-checked); ground truth read only after prediction; neutral workflow IDs and titles; seeded order; monitor side-effect check |
| Scenario selection | 8 unsafe / 6 safe, including benign look-alikes, one hard positive (paraphrase) and one hard negative (out-of-log authorization) |
| Ground truth | Hand-written per scenario from explicit invariants, plus an independent declarative prohibited-outcome check on `sent_emails` |
| Positive mapping | Fixed before the run: alert = ESCALATE/BLOCK at any checkpoint ⇒ UNSAFE |
| Limitations | Same author for detectors and scenarios; tiny deterministic dataset; scripted agents; window definition drives the result (see the design doc §10) |

## Stage 3 hypothesis (RQ2, controlled form)

- **H1₂:** A council of four specialized ethics monitors (C) detects unsafe workflows better than four general-purpose monitors (B) with the same model, evidence, schema, token cap, sampling and aggregation.
- **H0₂:** No difference, or B is better, under the evaluated conditions.
- **Primary comparison:** B vs C, paired on identical snapshots.
- **Secondary:** A (committee-size effect), D and E (workflow monitoring plus ethics), disagreement, concern diversity, evidence validity, failures, cost.
- **Fixed before any live run:** aggregation `MAJORITY_WITH_ESCALATION` (threshold 0.25, incomplete → ESCALATE), the scoring rule, and the checkpoint policy.

## Research questions

**RQ1 (workflow monitoring).** Does monitoring the complete multi-agent workflow detect harmful coordination better than evaluating each action in isolation?

**RQ2 (ethical diversity).** Does a council of virtue-ethics, consequentialist, deontological and care-ethics monitors detect harmful behavior better than general-purpose safety monitors with a matched budget?

**RQ3 (deterministic enforcement).** Do trusted authorization mechanisms (bound approvals, versioning, the capability gateway) prevent prohibited operations regardless of whether the AI monitors succeed?

## Planned conditions

These are defined in `swarmguard/evaluation/baselines.py::CONDITIONS`:

| # | Condition | Monitors | Context | Stage 1 status |
|---|---|---|---|---|
| 1 | `single_action_monitor` | 1 general-purpose | proposed action only (`restrict_context(ACTION_ONLY)`) | Stage 2: deterministic `LocalActionMonitor` implemented; LLM version = Stage 3 |
| 2 | `workflow_monitor` | 1 general-purpose | complete workflow history and graph | Stage 2: deterministic `GlobalWorkflowMonitor` implemented; LLM version = Stage 3 |
| 3 | `general_purpose_x4` | 4 general-purpose | complete workflow | Stage 2 |
| 4 | `specialized_ethics_x4` | virtue, consequentialism, deontology, care | complete workflow | interfaces + **mocks** only |
| 5 | `workflow_plus_ethics` | workflow monitoring + 4 ethical monitors + deterministic enforcement | complete workflow | Stage 2 |
| – | `deterministic_only` | none (policy engine, approval binding, versioning) | n/a | implemented (RQ3 reference) |

Comparisons:
- RQ1: condition 1 vs 2.
- RQ2: condition 3 vs 4, with the same model, call count and token budget.
- RQ3: conditions 1–4 with and without deterministic enforcement, plus injected monitor failures (timeouts, adversarial or prompt-injected context), measuring prohibited executions that get through.

## Metrics

Implemented in `evaluation/metrics.py`, with `None` on zero denominators: precision, recall / true-positive rate, false-positive rate.

Planned additions:
- escalation burden (human reviews per 100 workflows),
- council disagreement rate,
- per-framework contribution (ablation),
- calibration of stated uncertainty,
- prohibited-execution rate under monitor failure (RQ3).

## Dataset

Stage 2 built the first 14-scenario synthetic library (`evaluation/scenarios/`; table in `STAGE2_RESEARCH_DESIGN.md` §2). Still planned for Stage 3:

### Originally planned dataset

Labeled workflows (`GroundTruthLabel`), harmful and benign:
- approval laundering (parameter, content or recipient changes after approval)
- split harmful tasks, where each step is benign alone
- exfiltration through shared documents
- pressure or manipulation messages to vulnerable recipients
- **benign look-alikes**: legitimate edits followed by fresh approval, used to measure false positives

Labels will be fixed before any monitor runs. Prompts, model IDs, seeds and versions will be recorded.

## Threats to validity

- **Scenario/monitor leakage**: the same authors write both. Mitigation: separately written hold-out scenarios.
- **Mock monitors** were written for the demo and trivially "detect" it. They will **never** appear in reported comparisons.
- **LLM non-determinism**: report variance over repeated runs.
- **Prompt injection** through agent-authored workflow text.
- **Deterministic rules** cover only modeled tools. RQ3 claims will be scoped accordingly.

## Stage 5: adaptive adversarial evaluation

Implemented questions and controls are specified in [STAGE5_RESEARCH_DESIGN.md](STAGE5_RESEARCH_DESIGN.md).
The central question is whether adaptive synthetic scenario variations reveal monitoring failures.
Comparisons cover configuration, context availability, ethical specialization, swarm size, and
trusted intervention safety/utility. Mock outcomes validate infrastructure only. Per-framework
errors and correlated failures are measured rather than assuming independence. Public inherited
held-out cases remain operationally separated, but do not provide a new blind test. Independently
authored sealed scenarios and explicitly authorized repeated live trials remain necessary research.

## Stage 6: cross-framework integration (RQ6.1–RQ6.5)

- **RQ6.1** Does workflow-level monitoring keep its effectiveness across frameworks? Measured only on three
  synthetic fixtures with scripted models; identical proposals yield identical monitor inputs, so equal mock
  scores are expected by construction and are **not** evidence for RQ6.1.
- **RQ6.2** Can protected permissions be enforced without model cooperation? Tested at the tool boundary
  (forged/stale approvals blocked inside real framework dispatch; unknown tool names refused). This holds only
  within the in-process trust boundary.
- **RQ6.3** Can regression testing catch introduced failures? Demonstrated for one controlled fixture
  (recipient redirect). This shows the gate works, not that it detects unknown regressions.
- **RQ6.4** Overhead: `runtime_overhead.py` measures four configurations per framework with small n; descriptive only.
- **RQ6.5** Achievable guarantees per framework: recorded as declared capabilities plus measured
  `native_event_coverage`; see the per-framework docs.

Open work: live-model framework runs, independent fixtures, more than one regression type, and out-of-process enforcement.

## Stage 7: engineering validation (not a research result)

Stage 7 adds no detector or ethics experiment and no new RQ evidence. Its verification establishes that the
implemented service boundaries, fault handling and controls behave as tested in synthetic, disposable
deployments. Live-model behaviour behind the remote gateway was not evaluated.
