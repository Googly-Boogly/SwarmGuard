# SwarmGuard reproducibility bundle

Synthetic data; no live-model claims.

Verify without model calls: `python run_research.py --verify-bundle PATH_TO_BUNDLE`
Inspect `experiment.json` for exact observations, decisions, graphs, failures and labels.
Fresh deterministic run: `python run_research.py --config PATH_TO_BUNDLE/configuration.json --import-dataset PATH_TO_BUNDLE/dataset_manifest.json --output NEW_DIRECTORY`
Use the recorded source revision and dependency versions. Timestamps, latency and opaque approval tokens are not byte-reproducible. Recorded replay makes no model requests. Live re-execution is never exact.
