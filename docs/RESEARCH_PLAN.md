# Research Plan

*SwarmGuard: Evaluating Multi-Framework Ethical Oversight for Autonomous Multi-Agent Systems*

> **Status: no experiments have been run. No results are reported here.** Stage 1 ships infrastructure plus two scripted acceptance scenarios. Their outcomes are plumbing checks, not data.

## Central problem

Can several AI agents produce a harmful outcome through actions that are each acceptable on their own, and can workflow-level monitoring detect it? The Stage 1 laundering scenario illustrates the pattern; it does not test it. Research updates a shared contact field, Operations applies it to an already-approved email, and Operations then sends it. Each step passes individual authorization checks.

## Research questions

**RQ1 (workflow monitoring).** Does monitoring the complete multi-agent workflow detect harmful coordination better than evaluating each action in isolation?

**RQ2 (ethical diversity).** Does a council of virtue-ethics, consequentialist, deontological and care-ethics monitors detect harmful behavior better than general-purpose safety monitors with a matched budget?

**RQ3 (deterministic enforcement).** Do trusted authorization mechanisms (bound approvals, versioning, the capability gateway) prevent prohibited operations regardless of whether the AI monitors succeed?

## Planned conditions

These are defined in `swarmguard/evaluation/baselines.py::CONDITIONS`:

| # | Condition | Monitors | Context | Stage 1 status |
|---|---|---|---|---|
| 1 | `single_action_monitor` | 1 general-purpose | proposed action only (`restrict_context(ACTION_ONLY)`) | context restriction implemented; monitor = Stage 2 |
| 2 | `workflow_monitor` | 1 general-purpose | complete workflow history and graph | Stage 2 |
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

## Planned dataset (hackathon work, not built yet)

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
