# Researcher guide

Run `python run_research.py --provider mock`. The command persists a laboratory database and exclusive bundles under results/stage8. Use `--dataset swarmguard_autonomous --mode MOCK_AUTONOMOUS --question RQ4` for observation-driven mock policies.

The default compares all seven monitor configurations. Exact configuration JSON, dataset definition/hash, source fingerprint, actual revision, prompt hashes, environment versions, prediction CSV, failure accounting, trajectories, graph, report and checksums are exported.

In the frontend Experiment Designer, **Maximum total trials** defaults to **2**, across all scenarios and repetitions. **Trials per scenario** controls repetitions before that cap. The displayed planned count is the smaller of dataset size × repetitions and the total limit. Seed changes ordering, not the limit. The backend freezes the seeded schedule before inference, and reports account for only that planned subset; a small subset may omit safe or unsafe cases. Monitor assessments and scripted enforcement replays remain nested within each trial. CLI configurations without `max_total_trials` retain the full dataset schedule.

Laboratory statistics show a compact comparison and one row per trial (ten per page). RQ1 defaults to local versus global monitoring, with recall, false-positive rate, denominators and missing assessments visible. All-monitor comparisons, raw trajectories and validation records are collapsed and rendered only when opened. **GENERATE REPORT** produces a short summary; it does not print dataset definitions, all pair combinations or manifests. Existing experiments use this view without rerunning. Full evidence and the original detailed report remain in the reproducibility bundle. The report API also accepts `format: "full"`; explicit section requests retain the detailed format. Existing bundle generation and verification remain unchanged.

Verify with `python run_research.py --verify-bundle results/stage8/experiment_001`. Reproduce using the bundle README and the recorded code revision. Recorded replay requires the source experiment in the same laboratory database. It reuses recorded outputs without calls. Fresh mock runs preserve declared behavior, not wall-clock timings or opaque approval tokens.

Use existing run_framework_evaluation.py for external frameworks and run_ethics_evaluation.py for separate provider studies. No paid API study was executed for this change.
