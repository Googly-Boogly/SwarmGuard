# Foundation Work Record (Stage 1)

This file records the foundation software that exists in this repository, and separates it from hackathon research, future components, mock assessments, and experimental findings.

The event is the Apart Research Swarm Alignment Hackathon, 6–8 November 2026.

## Timing: UNVERIFIED, and probably not pre-hackathon

| Evidence | What it shows |
|---|---|
| Git history | **None.** There was no repository, so there are no commits or commit timestamps. |
| Filesystem timestamps | All Stage 1 files were created or last modified on **2026-11-06**, between about 14:20 and 15:05 UTC (08:20–09:05 local, UTC−6), including a full revision. Filesystem times are weak evidence: they can be changed and they don't survive copying. |
| Hackathon window | 2026-11-06 is the **first day of the event**. The official start time was not available when this was written. |

**Classification:** the timing is **unverified**. The only available evidence (filesystem times) places this work *on the first day of the hackathon*. Unless the organizers' official start time shows otherwise, treat Stage 1 as **work done during the hackathon window**, not as pre-existing work. Being described in a prompt before the event does not make code pre-hackathon.

To create verifiable provenance from now on, commit the repository (`git init && git add -A && git commit`) and push it to a timestamped remote.

## What exists (foundational software)

- Package `swarmguard/`: Pydantic v2 models and SQLite persistence split into three trust boundaries, with a repository layer
- Three deterministic scripted agents with trusted identities, role permissions, and a sealed registry
- A shared workspace with optimistic-concurrency versioning and per-version snapshots
- A deterministic policy engine with stable reason codes and strict schemas
- A human-approval manager: two-stage canonical SHA-256 binding, HMAC signature, expiry, single use, invalidation, approver-only credentials
- A capability gateway: fail-closed, HMAC single-use execution permits, atomic protected execution
- A workflow monitor (explicit parent event references, summaries) and a workflow dependency graph
- Ethics-council interfaces, a unanimity aggregation rule, and four mock monitors
- A read-only FastAPI API, Docker and Compose files
- Evaluation interfaces (scenarios, ground-truth labels, experiment config, predictions, metrics) and two demo scenarios
- A pytest suite (98 tests when this was written) and `demo.py`

## Mock assessments

**All four ethical monitors are mocks**: `MockVirtueEthicsMonitor`, `MockConsequentialistMonitor`, `MockDeontologicalMonitor` and `MockCareEthicsMonitor`.

- Each is one or two hand-written rules over workflow events or keywords, written to exercise the council pipeline on the demo scenario.
- They do no ethical reasoning and produce no LLM output.
- Their output is labeled `is_mock=True` and starts with `[MOCK heuristic, not an LLM assessment]`.
- They must not appear in any reported comparison.

## Experimental findings

**None.**
- RQ1, RQ2 and RQ3 have not been tested.
- The two demo scenarios show the infrastructure works; they are not evidence for any hypothesis.
- The mock council "flags" the laundering scenario because its rules were written for that scenario.
- The only runner output (`EvaluationResult`) is flagged `is_research_result=False`.

## Future research components (not implemented)

- LLM-backed ethics monitors (four specialized) and general-purpose baseline monitors
- LLM-driven agents
- A labeled scenario library with benign look-alikes, and the RQ1–RQ3 experiments, including monitor-failure injection
- Process-level agent isolation, a tamper-evident audit log, a real approval UI, and graph visualization

## Hackathon work

Work done during the competition must be logged in a separate file (e.g. `HACKATHON_WORK.md`) with commit references. Per the timing note above, the Stage 1 foundation itself may belong in that record too.
