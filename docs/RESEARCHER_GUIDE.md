# Researcher guide

Run `python run_research.py --provider mock`. The command persists a laboratory database and exclusive bundles under results/stage8. Use `--dataset swarmguard_autonomous --mode MOCK_AUTONOMOUS --question RQ4` for observation-driven mock policies.

The default compares all seven monitor configurations. Exact configuration JSON, dataset definition/hash, source fingerprint, actual revision, prompt hashes, environment versions, prediction CSV, failure accounting, trajectories, graph, report and checksums are exported.

Verify with `python run_research.py --verify-bundle results/stage8/experiment_001`. Reproduce using the bundle README and the recorded code revision. Recorded replay requires the source experiment in the same laboratory database. It reuses recorded outputs without calls. Fresh mock runs preserve declared behavior, not wall-clock timings or opaque approval tokens.

Use existing run_framework_evaluation.py for external frameworks and run_ethics_evaluation.py for separate provider studies. No paid API study was executed for this change.
