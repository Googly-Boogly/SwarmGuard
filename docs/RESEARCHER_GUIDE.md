# Researcher guide

Run `python run_research.py --provider mock`. The command persists a laboratory database and exclusive bundles under results/stage8. Use `--dataset swarmguard_autonomous --mode MOCK_AUTONOMOUS --question RQ1` for observation-driven mock policies.

The registry (`rq-registry-3`) contains RQ1–RQ3 only. RQ2 asks: "Does a four-framework Ethics Council detect multi-agent safety violations more effectively than an equivalent group of general-purpose safety monitors?" (reworded in `rq-registry-3`; experiments created under `rq-registry-2` keep the earlier wording); new experiments cannot select RQ4–RQ7. Experiments created earlier keep rendering and verifying with the question text they were created under (`rq-registry-1`).

The default compares all seven monitor configurations. Exact configuration JSON, dataset definition/hash, source fingerprint, actual revision, prompt hashes, environment versions, prediction CSV, failure accounting, trajectories, graph, report and checksums are exported.

In the frontend Experiment Designer, **Maximum total trials** defaults to **2**, across all scenarios and repetitions. **Trials per scenario** controls repetitions before that cap. The displayed planned count is the smaller of dataset size × repetitions and the total limit. Seed changes ordering, not the limit. The backend freezes the seeded schedule before inference, and reports account for only that planned subset; a small subset may omit safe or unsafe cases. Monitor assessments and scripted enforcement replays remain nested within each trial. CLI configurations without `max_total_trials` retain the full dataset schedule.

Laboratory statistics show a compact comparison and one row per trial (ten per page). RQ1 defaults to local versus global monitoring, with recall, false-positive rate, denominators and missing assessments visible. All-monitor comparisons, raw trajectories and validation records are collapsed and rendered only when opened. **GENERATE REPORT** produces a short summary; it does not print dataset definitions, all pair combinations or manifests. Existing experiments use this view without rerunning. Full evidence and the original detailed report remain in the reproducibility bundle. The report API also accepts `format: "full"`; explicit section requests retain the detailed format. Existing bundle generation and verification remain unchanged.

Verify with `python run_research.py --verify-bundle results/stage8/experiment_001`. Reproduce using the bundle README and the recorded code revision. Recorded replay requires the source experiment in the same laboratory database. It reuses recorded outputs without calls. Fresh mock runs preserve declared behavior, not wall-clock timings or opaque approval tokens.

Use existing run_framework_evaluation.py for external frameworks and run_ethics_evaluation.py for separate provider studies. No paid API study was executed for this change.

## RQ1 laboratory (browser)

Open **Research → RQ1 Laboratory** (`/research/rq1`). **NEW EXPERIMENT** opens the configuration; **USE RECOMMENDED RQ1 SETTINGS** selects the pre-registered procedure (frozen held-out dataset, all 24 workflows, seed 0, one trial) and **Use pilot settings** selects the two development pilot scenarios. **VALIDATE METHODOLOGY** runs monitor-free checks on the server; **RUN RQ1 EXPERIMENT** is enabled only after the current settings validate without critical problems. The experiment page shows live progress and recorded events, then the results tabs. Raw Data has checkpoint-level and workflow-level views with server-side filters, sorting, paging, quick views (for example "Local missed unsafe workflows", "Global false positives"), copy buttons and a record panel showing the exact monitor input and output, ground truth, gateway decision and outcome. Results are stored in the console database and stay available after restarts. Runs are deterministic and synthetic; the decision rule decides H1 only for the pre-registered procedure.

### Pre-registered hypothesis verdicts

The decision rules written in the RQ1 protocols are transcribed as data in `research/rq1/hypothesis_registry.json` (`rq1-hypotheses-1`). Each entry quotes its source protocol and pins that document's sha256. `swarmguard/research/hypotheses.py` evaluates every rule registered for an experiment's dataset against the experiment's **stored** record and result. It never reruns a workflow or writes anything. Arithmetic is exact over the stored TP/FP/TN/FN counts.

Each rule gets one verdict:
- **SUPPORTED:** every condition holds. This is descriptive support on this constructed dataset only.
- **H0_RETAINED:** at least one condition fails, including an integrity condition, as the protocols state.
- **INCONCLUSIVE:** the protocol says UNKNOWN predictions make the run inconclusive, or a metric is undefined (zero denominator).
- **NOT_APPLICABLE:** the run is not the registered procedure (wrong dataset or hash, a subset, the wrong seed or mode, or a synthetic provider). Its conditions are shown for orientation only.

Mock LLM outputs can never support an LLM hypothesis. In an LLM run, a rules-arm hypothesis is marked `secondary`.

Where to see verdicts:
- **Browser:** the experiment's Overview and Research Findings tabs.
- **API:** `GET /api/research/rq1/experiments/{id}/hypotheses` (researcher session, read-only).
- **CLI**, offline:
  - `python run_hypothesis_check.py --snapshot research/rq1/experiment_006_snapshot.json`
  - `python run_hypothesis_check.py --db <copy of console db> --all` (opened with `mode=ro`)
  - `--output` never overwrites an existing file.

The older `decision_rule` stored at run time covers the rules arms only. It is kept unchanged as a historical record.

**Adding a new experiment:** add its hypothesis entry before the protocol is frozen, then list `research/rq1/hypothesis_registry.json` in that experiment's freeze `files`. Never edit an existing entry after outcomes are seen. A correction needs a new entry and a new `registry_version`.

### RQ1 with LLM monitors

The configuration's **LLM monitors** section adds LLM Local and LLM Global (one `GeneralSafetyMonitor` on the Local vs. Global view of the same snapshot; protocol `docs/RQ1_LLM_PROTOCOL.md`). **Mock** runs the LLM pipeline with a synthetic responder (no model, never evidence). **Live** calls the configured OpenAI model and is offered only when the console was started with `SWARMGUARD_CONSOLE_LIVE_LLM=1` and `.env` has `OPENAI_API_KEY`, `SWARMGUARD_OPENAI_MODEL` and positive `SWARMGUARD_PRICE_*_USD_PER_MTOK`; each run also needs a request cap, a dollar cap and the paid-call confirmation. Validation shows the number of calls and the worst-case cost before anything runs. For example:

```bash
SWARMGUARD_CONSOLE_LIVE_LLM=1 python -m uvicorn console_main:app --host 127.0.0.1 --port 8000 --ws-max-size 16384 --limit-concurrency 32
```
