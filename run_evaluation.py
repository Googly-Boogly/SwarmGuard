"""Reproducible Stage 2 research evaluation: Local Action Monitor vs. Global Workflow Monitor.

    python run_evaluation.py                                  # results/stage2_<UTC timestamp>/
    python run_evaluation.py --output results/stage2 --experiment-id run_001 --seed 0
    python run_evaluation.py --scenarios distributed_approval_001 legitimate_collaboration_001

Every printed metric is computed from this run's monitor predictions. Undefined metrics
(zero denominators) are printed as "undefined". The scenarios and monitors are
deterministic and synthetic, so the results validate infrastructure under controlled
conditions. They are not evidence about real autonomous agents.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from swarmguard.clock import SystemClock
from swarmguard.config import configure_logging
from swarmguard.evaluation.experiment import Stage2ExperimentConfig
from swarmguard.evaluation.metrics import fmt
from swarmguard.evaluation.results import save_results
from swarmguard.evaluation.runner import run_stage2_experiment

BAR = "=" * 50


def git_revision() -> str | None:
    """`git describe --always --dirty`, or None. Local read-only git call; never fabricated."""
    if shutil.which("git") is None:
        return None
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty", "--abbrev=40"], capture_output=True,
                             text=True, timeout=10, cwd=Path(__file__).resolve().parent, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout.strip() or None


async def main(output: Path, experiment_id: str | None, seed: int, scenarios: list[str] | None) -> int:
    clock = SystemClock()
    experiment_id = experiment_id or f"stage2_{clock.now().strftime('%Y%m%dT%H%M%SZ')}"
    config = Stage2ExperimentConfig(experiment_id=experiment_id, seed=seed, scenario_ids=scenarios)
    results = await run_stage2_experiment(config, clock=clock, revision=git_revision())
    path = save_results(results, output)
    det, prev, paired = results.metrics["detection"], results.metrics["prevention"], results.metrics["paired"]

    print(f"{BAR}\nSWARMGUARD RESEARCH EVALUATION\n{BAR}\n")
    print(f"Scenarios evaluated: {results.metadata['scenario_count']}  ({results.metrics['dataset']['label']})")
    print(f"Ruleset: {results.metadata['ruleset_version']}   seed: {seed}   revision: {results.metadata['code_revision']}")
    print(f"Positive = {results.config['positive_mapping'].split(';')[0]}\n")
    for name, title in (("local_action", "Local Monitor"), ("global_workflow", "Global Monitor")):
        if name not in det:
            continue
        m = det[name]
        lat = m["latency"]
        print(f"{title}:")
        print(f"Precision: {fmt(m['precision'])}")
        print(f"Recall: {fmt(m['recall'])}")
        print(f"False Positive Rate: {fmt(m['false_positive_rate'])}")
        print(f"F1: {fmt(m['f1'])}   Accuracy: {fmt(m['accuracy'])}   "
              f"(TP={m['tp']} FP={m['fp']} TN={m['tn']} FN={m['fn']}; "
              f"predicted INSUFFICIENT_INFORMATION={m['predicted_insufficient_information']})")
        print(f"Detection lead (steps before critical action, detected unsafe only): mean {fmt(lat['mean_lead_steps'], 2)}, "
              f"range [{fmt(lat['min_lead_steps'])}, {fmt(lat['max_lead_steps'])}]; undetected {lat['undetected']}")
        print(f"Alerts on SAFE-scenario checkpoints: {m['safe_checkpoint_alerts']} of {m['safe_checkpoints']}\n")
    if paired:
        u, s = paired["unsafe"], paired["safe"]
        print(f"Paired (unsafe, n={u['n']}): both={u['both_flagged']} only_local={u['only_local']} "
              f"only_global={u['only_global']} neither={u['neither']}")
        print(f"Paired (safe,   n={s['n']}): both={s['both_flagged']} only_local={s['only_local']} "
              f"only_global={s['only_global']} neither={s['neither']}\n")
    print("Prevention (separate runs; prohibited outcome actually avoided):")
    for cfg, p in prev.items():
        print(f"  {cfg:<20} prevention rate {fmt(p['prevention_rate'])} ({p['prevented']}/{p['unsafe_scenarios']}; "
              f"policy block {p['prevented_with_policy_block']}, guard-only {p['prevented_with_guard_escalation_only']}); "
              f"safe runs escalated {p['safe_scenarios_with_guard_escalation']}/{p['safe_scenarios']}")
    print(f"\nResults saved:\n{path.resolve()}\n")
    print("Note: deterministic synthetic benchmark; preliminary infrastructure validation only.\n" + BAR)
    return 0


if __name__ == "__main__":
    configure_logging("WARNING")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scenarios", nargs="*", default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.output, args.experiment_id, args.seed, args.scenarios)))
