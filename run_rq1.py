"""RQ1 experiment command: Local Action Monitor vs. Global Workflow Monitor (deterministic, no network).

    python run_rq1.py manifest --partition held_out           # print the dataset manifest hash
    python run_rq1.py validate --partition held_out           # monitor-free dataset validity check
    python run_rq1.py pilot --experiment-id rq1_pilot_001     # 2 development scenarios + all checks
    python run_rq1.py run --partition held_out --experiment-id rq1_primary_001 --expect-hash <hash>

Outputs go to research/rq1/experiments/<experiment_id>/ and are never overwritten.
See docs/RQ1_EXPERIMENT_PROTOCOL.md. No LLM, API key or paid call is used by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

from run_evaluation import git_revision
from swarmguard.clock import SystemClock
from swarmguard.evaluation import rq1
from swarmguard.evaluation.experiment import Stage2ExperimentConfig
from swarmguard.evaluation.metrics import fmt
from swarmguard.evaluation.runner import run_stage2_experiment

ROOT = Path("research/rq1")
EXPERIMENTS = ROOT / "experiments"


def _print_table(analysis: dict) -> None:
    p = analysis["primary"]
    lo, gl = p[rq1.LOCAL], p[rq1.GLOBAL]
    rows = [("True positives", "tp"), ("False positives", "fp"), ("False negatives", "fn"), ("True negatives", "tn"),
            ("Recall", "recall"), ("Precision", "precision"), ("False-positive rate", "false_positive_rate"),
            ("F1", "f1"), ("Accuracy", "accuracy")]
    print(f"{'Metric':<22}{'Local':>12}{'Global':>12}")
    for title, key in rows:
        print(f"{title:<22}{fmt(lo.get(key)):>12}{fmt(gl.get(key)):>12}")
    lat = analysis["detection_latency"]
    print(f"{'Median lead (ckpts)':<22}{fmt(lat[rq1.LOCAL]['median_lead_checkpoints']):>12}"
          f"{fmt(lat[rq1.GLOBAL]['median_lead_checkpoints']):>12}")
    u = analysis["paired"]["unsafe"]
    print(f"\nPaired unsafe (n={u['n']}): both {u['both']}, only global {u['only_global']}, "
          f"only local {u['only_local']}, neither {u['neither']}; exact McNemar p = "
          f"{fmt(u['exact_mcnemar_p_two_sided'], 4)}")
    s = analysis["paired"]["safe"]
    print(f"Paired safe   (n={s['n']}): flagged by both {s['both']}, only global {s['only_global']}, "
          f"only local {s['only_local']}, neither {s['neither']}")


async def _experiment(partition: str, experiment_id: str, scenario_ids: list[str] | None) -> Path:
    manifest = rq1.dataset_manifest(partition)
    if scenario_ids is not None:
        manifest = {**manifest, "scenarios": [s for s in manifest["scenarios"] if s["scenario_id"] in scenario_ids]}
    if (EXPERIMENTS / experiment_id).exists():
        raise FileExistsError(f"{EXPERIMENTS / experiment_id} already exists; choose a new experiment id")
    scenarios = rq1.scenarios_for(partition)
    config = Stage2ExperimentConfig(experiment_id=experiment_id, seed=rq1.PRIMARY_SEED, scenario_ids=scenario_ids)
    revision = git_revision()
    results = await run_stage2_experiment(config, clock=SystemClock(), revision=revision, scenarios=scenarios)
    results = results.model_copy(update={"metadata": {
        **results.metadata, "rq1_protocol_version": rq1.PROTOCOL_VERSION, "partition": partition,
        "dataset_hash": manifest["content_hash"], "execution_mode": "DETERMINISTIC_SIMULATION",
        "live_model_calls": 0, "environment": rq1.environment()}})
    verification = await rq1.verify_observations(results, scenarios)
    analysis = rq1.analyze(results, manifest)
    out = rq1.export_run(results, manifest, verification, analysis, EXPERIMENTS)
    print(f"Experiment {experiment_id}: partition={partition}, n={analysis['n_workflows']}, "
          f"dataset hash={manifest['content_hash'][:16]}…, revision={revision}")
    print(f"Observation checks: {'PASS' if verification['checks_passed'] else 'FAIL'} "
          f"({verification['contexts_checked']} contexts)")
    for f in verification["failures"]:
        print(f"  FAIL {f}")
    _print_table(analysis)
    print(f"\nArtifacts: {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--partition", choices=rq1.PARTITIONS, default="held_out")
        p.add_argument("--write", type=Path, help="write the JSON to this path (must not exist)")
    pilot = sub.add_parser("pilot")
    pilot.add_argument("--experiment-id", required=True)
    run = sub.add_parser("run")
    run.add_argument("--partition", choices=rq1.PARTITIONS, default="held_out")
    run.add_argument("--experiment-id", required=True)
    run.add_argument("--expect-hash", required=True, help="frozen dataset content hash from the protocol")
    args = parser.parse_args()

    if args.command in ("manifest", "validate"):
        value = rq1.dataset_manifest(args.partition) if args.command == "manifest" else asyncio.run(
            rq1.validate_dataset(args.partition))
        if args.write:
            rq1.write_json_exclusive(args.write, value)
        if args.command == "manifest":
            print(json.dumps({"partition": value["partition"], "dataset_version": value["dataset_version"],
                              "n": value["n"], "composition": value["composition"],
                              "content_hash": value["content_hash"]}, indent=2))
            return 0
        print(json.dumps({k: value[k] for k in ("partition", "n", "valid", "all_valid", "monitors_evaluated")}))
        for row in value["scenarios"]:
            if not row["valid"]:
                print(f"  INVALID {row['scenario_id']}: {row.get('problems') or row.get('error')}")
        return 0 if value["all_valid"] else 1

    if args.command == "run":
        actual = rq1.dataset_manifest(args.partition)["content_hash"]
        if actual != args.expect_hash:
            print(f"Dataset hash {actual} does not match the frozen hash {args.expect_hash}; refusing to run.",
                  file=sys.stderr)
            return 2
    partition = "development" if args.command == "pilot" else args.partition
    ids = list(rq1.PILOT_SCENARIOS) if args.command == "pilot" else None
    try:
        asyncio.run(_experiment(partition, args.experiment_id, ids))
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:  # a failed evaluation is reported, never converted into results
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
