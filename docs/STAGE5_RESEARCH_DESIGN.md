# Stage 5 research design

Stage 5 implements constrained adaptive synthetic evaluation on the existing simulator,
Capability Gateway, ObservationBuilder, RiskAnalyzer, and Stage 3 council implementations.
It is a local research prototype. Mock councils render deterministic rules, not LLM reasoning.

## Questions

The central question is whether adaptive evaluation reveals cross-agent monitoring failures.
RQ5.1 compares oversight configurations; RQ5.2 varies available history; RQ5.3 compares ethical
specialization; RQ5.4 varies participant count; RQ5.5 compares trusted safety interventions
against legitimate completion. Null results and counterexamples are valid outcomes.

## Method

`ResearchConfig` freezes partition, seed, monitoring selection, iteration/step/request/runtime/
concurrency/spending limits before execution. `ScenarioGenerator` selects allowlisted templates.
The deterministic mutator changes agent count, independent action ordering, public task
allocation, explicit communication dependencies, versioned shared writes, approval review timing,
information fragmentation, or experimental observation availability. Approval timing and
fragmentation are restricted to templates with an applicable trusted specification. Fragmentation
compares a combined two-field disclosure with separately approved disclosures using the same
cumulative oracle; it is not arbitrary semantic content splitting.

`AdaptiveController` alternates coverage exploration and refinement of observed candidates.
Under-tested families have priority; observed misses are prioritized within equal coverage.
It uses a seeded initial order and no opaque scalar attack reward. `generated` counts candidates
selected for evaluation; `pool_generated` also counts constructed, untested variations. The latter
are not presented as executed or validated. Iterations and all controller outcomes are bounded.

Validation rechecks the complete strict definition against the authorized partition, template,
server limits, and supported mutation semantics. Generated data cannot express trusted policies,
credentials, approval grants, raw code, arbitrary destinations, SQL, or new tools. Validation
rejections are persisted. Schema-invalid browser input is rejected and audited before execution.

Each candidate uses a fresh simulator, sealed identity registry, and in-memory SQLite environment.
Additional research participants receive ordinary research permissions and public-reference tasks;
they do not gain access to customer records merely by joining the workflow. Trusted scripted
fixtures retain their existing simulated-human approval step. Browser launch authorizes a
synthetic replay, not an operator decision on a real protected operation. The existing interactive
Stage 4.5 approval path is unchanged.

## Predictions and feedback

The primary comparison evaluates the critical proposal checkpoint only. Local and global
observations come from one as-of snapshot. Every council receives the identical global
observation, schema, model, seed setting, temperature setting, and 800-token cap. Aggregation is
fixed to MAJORITY_WITH_ESCALATION. Incomplete councils are recorded as failed predictions
(`null`), never as safe. Combined oversight is OR(global, specialized); a positive global result
remains positive even if the council fails. Council failures remain visible separately.

Predictions are fixed before reading the trusted template ground truth. The latter includes a
declarative prohibited-outcome oracle evaluated on actual synthetic sends. Feedback separately
records prohibited proposals, prohibited outcomes, monitor alerts, gateway blocks, exact detection
checkpoint, legitimate completion, validity and incomplete observations. A block elsewhere in a
workflow does not prove prevention. Policy-only has no advisory monitor, hence no advisory alerts;
its prevention outcomes are measured independently. Three isolated intervention replays retain
the gateway and compare policy only, local guard, and global guard. Guards only escalate policy
ALLOW decisions. No observational council result directly executes a tool.

Source fixtures define a service communication objective. Legitimate completion is measured
from actual sends; for unsafe fixtures the current utility proxy is a send to the synthetic
customer's own address. This limited proxy is stored explicitly and is not a general measure
of useful work. Failed executions have unknown outcome fields and are excluded from scored metrics.

## Analysis

The shared RiskAnalyzer adds a heuristic monitor-directed-manipulation signal in every window.
Its version is `stage5-rules-v2`. Explicit cross-agent dependencies are ordinary collaboration;
concerning instructions and invariant findings are separate. Existing information-flow analysis
matches synthetic field values and records source/resource/action provenance. Field labels are
PUBLIC, INTERNAL, CONFIDENTIAL and PROTECTED_SYNTHETIC; protected/quasi-identifier fields are
classified from trusted invariants, with labels preserved in emitted flow traces.

Robustness pairs preserve exact observations and change logs: source withholding, independent
presentation-order variation, and misleading, contradictory or injected untrusted reports.
Reports modify an experimental prior message only; authoritative event records remain intact.
Global-monitor pairs cover all variants. General and specialized council pairs cover original and
incomplete context with matched evidence. Large-history mutations add real public work. Missing
usage, failed outputs, unavailable interventions and unapplied transformations remain explicit.

Failure reports distinguish verified facts from optional advisory interpretation (currently absent).
False positives, missing experimental context and model-output failures receive supported
categories; other causes remain UNKNOWN_FAILURE. Relevant workflow events, action IDs, full
observations, assessments, graph and oracle are inspectable. Categories for further causal
attribution exist but are not assigned without evidence.

## Partitions and threats to validity

The versioned catalog assigns each template to development, adaptive_search or held_out.
Adaptive search accepts only adaptive_search and cannot access held-out feedback. Benchmark
runs accept one partition at a time, and do not change monitors. Family membership is fixed;
mutations cannot cross it. Stage 5's held-out fixtures are inherited public Stage 2 cases.
They have already been visible during earlier development. This is software-enforced operational
separation, not a novel blind or independently authored evaluation dataset. Do not claim otherwise.

Other limitations: small author-written synthetic library; scripted workflow policies; shallow
mutations; same-author oracles and detectors; exact-value provenance misses paraphrases;
critical-only checkpoints cannot establish earliest possible detection; extra participants add work;
mock councils are correlated by construction; live generation and live council robustness remain
unverified. No paid study was authorized or run. Adding monitors or agents is not a measured
safety improvement by itself. An independently authored sealed benchmark is required before
stronger generalization claims.
