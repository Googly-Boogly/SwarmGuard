"""Stage 3 comparative evaluation (RQ2): general-purpose vs. specialized ethics councils.

    python run_ethics_evaluation.py --provider mock
    python run_ethics_evaluation.py --provider openai --checkpoint-policy critical --scenarios distributed_approval_001
    python run_ethics_evaluation.py --provider mock --experiment-id my_run --output results/stage3

Results go to results/stage3/<experiment_id>/ (an existing directory is never overwritten unless
--overwrite is given). The aggregation method and scoring are fixed in the config before the
run. MOCK runs are pipeline checks: every configuration receives identical synthetic answers, so
they say nothing about RQ2. LIVE runs need explicit configuration (see .env.example). A failed
live request is recorded as a failure and is never replaced by a mock answer.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from run_evaluation import git_revision
from swarmguard.clock import SystemClock
from swarmguard.config import configure_logging
from swarmguard.ethics.aggregator import AggregationMethod
from swarmguard.ethics.synthetic import rules_responder
from swarmguard.evaluation.ethics_comparison import (
    CONFIGURATIONS,
    EthicsExperimentConfig,
    ExperimentExistsError,
    run_ethics_experiment,
    save_ethics_results,
)
from swarmguard.evaluation.metrics import fmt
from swarmguard.llm.configuration import LLMConfigurationError, LLMSettings, load_env_file
from swarmguard_providers.factory import build_provider

BAR = "=" * 60


async def main(provider_name: str, output: Path, experiment_id: str | None, seed: int, scenarios: list[str] | None,
               checkpoint_policy: str, aggregation: str, configurations: list[str] | None, overwrite: bool = False) -> int:
    load_env_file()
    settings = LLMSettings.from_env(provider=provider_name)
    try:
        provider, guard = build_provider(settings, mock_responder=rules_responder)
    except LLMConfigurationError as exc:
        print(f"Live configuration incomplete, no request was made: {exc}", file=sys.stderr)
        return 2
    clock = SystemClock()
    experiment_id = experiment_id or f"ethics_{settings.provider}_{clock.now().strftime('%Y%m%dT%H%M%SZ')}"
    if (output / experiment_id).exists() and not overwrite:
        print(f"{output / experiment_id} already exists; refusing to overwrite (use --overwrite).", file=sys.stderr)
        return 2
    config = EthicsExperimentConfig(experiment_id=experiment_id, seed=seed, scenario_ids=scenarios,
                                    checkpoint_policy=checkpoint_policy,
                                    aggregation_method=AggregationMethod(aggregation),
                                    configurations=configurations or list(CONFIGURATIONS),
                                    # cover every permitted attempt plus backoff, so retries are not cut off
                                    council_timeout_seconds=settings.timeout_seconds * (settings.max_retries + 1)
                                    + 2 ** settings.max_retries + 5)
    results = await run_ethics_experiment(config, provider, settings, guard=guard, clock=clock,
                                          revision=git_revision())
    try:
        path = save_ethics_results(results, output, overwrite=overwrite)
    except ExperimentExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    m = results.metrics
    print(f"{BAR}\nSWARMGUARD STAGE 3 ETHICS EVALUATION ({'SYNTHETIC MOCK' if provider.is_synthetic else 'LIVE'})\n{BAR}")
    print(f"Provider: {provider.name}  model: {provider.model}  prompt: {results.metadata['prompts']['prompt_version']}")
    print(f"Aggregation (pre-registered): {config.aggregation_method.value}  checkpoints: {checkpoint_policy}  "
          f"scenarios: {results.metadata['scenario_count']} {m['dataset']['label']}")
    print(f"Scoring: {config.positive_mapping}\n")
    print(f"{'config':<28}{'prec':>7}{'recall':>8}{'FPR':>7}{'F1':>7}{'acc':>7}  TP/FP/TN/FN   failures  disagree")
    for cfg, d in m["detection"].items():
        op = m["operational"][cfg]
        print(f"{cfg:<28}{fmt(d['precision']):>7}{fmt(d['recall']):>8}{fmt(d['false_positive_rate']):>7}"
              f"{fmt(d['f1']):>7}{fmt(d['accuracy']):>7}  {d['tp']}/{d['fp']}/{d['tn']}/{d['fn']:<6}"
              f"{op['failed_assessments']:>6}/{op['monitor_calls']:<5}{fmt(op['council_disagreement_rate']):>9}")
    if m["paired_B_vs_C"]:
        u, s = m["paired_B_vs_C"]["unsafe"], m["paired_B_vs_C"]["safe"]
        print(f"\nPaired B vs C, unsafe (n={u['n']}): both={u['both']} only_B={u['only_general_x4']} "
              f"only_C={u['only_specialized_x4']} neither={u['neither']}")
        print(f"Paired B vs C, safe   (n={s['n']}): both={s['both']} only_B={s['only_general_x4']} "
              f"only_C={s['only_specialized_x4']} neither={s['neither']}")
    print(f"\nProvider ledger: {m['provider_ledger']}")
    if m["terminated_early"]:
        print("TERMINATED EARLY: a request or budget limit was reached; unattempted evaluations are recorded.")
    print(f"\nResults saved:\n{path.resolve()}\n")
    print(results.note + "\n" + BAR)
    return 0


if __name__ == "__main__":
    configure_logging("WARNING")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=("mock", "openai"), default="mock")
    parser.add_argument("--output", type=Path, default=Path("results/stage3"))
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scenarios", nargs="*", default=None)
    parser.add_argument("--checkpoint-policy", choices=("outbound", "critical"), default="outbound")
    parser.add_argument("--aggregation", choices=[a.value for a in AggregationMethod],
                        default=AggregationMethod.MAJORITY_WITH_ESCALATION.value)
    parser.add_argument("--configurations", nargs="*", choices=list(CONFIGURATIONS), default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.provider, args.output, args.experiment_id, args.seed, args.scenarios,
                              args.checkpoint_policy, args.aggregation, args.configurations, args.overwrite)))
