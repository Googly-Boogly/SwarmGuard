"""Stage 3 demonstration: the four-framework ethics council on a distributed approval-laundering workflow.

    python demo_stage3.py --provider mock                      # no key, no network (synthetic responses)
    python demo_stage3.py --provider mock --inject-failure care_ethics
    python demo_stage3.py --provider openai                    # needs OPENAI_API_KEY, SWARMGUARD_OPENAI_MODEL
                                                               # and a budget (see .env.example)

Every printed value comes from an executed assessment. In mock mode the "assessments" are the
Stage 2 deterministic rules rendered in the LLM schema (identical for every framework). They are
labelled SYNTHETIC and are not LLM reasoning. In live mode a configuration problem stops the demo
before any request; it never falls back to mock. The council is advisory: the deterministic gateway
decision is shown separately and is not changed by the council.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.config import configure_logging
from swarmguard.ethics.general_council import build_specialized_council
from swarmguard.ethics.prompts import prompt_manifest
from swarmguard.ethics.synthetic import rules_responder
from swarmguard.evaluation.scenarios.distributed_approval import DistributedApprovalScenario
from swarmguard.llm.configuration import LLMConfigurationError, LLMSettings, load_env_file
from swarmguard.llm.mock_provider import MockBehavior, MockScript, MockStep
from swarmguard.models.assessments import SPECIALIZED_FRAMEWORKS
from swarmguard_providers.factory import build_provider

BAR, RULE = "=" * 50, "-" * 50
TITLES = {"virtue_ethics": "VIRTUE ETHICS", "consequentialism": "CONSEQUENTIALISM", "deontology": "DEONTOLOGY",
          "care_ethics": "CARE ETHICS"}


def section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


async def main(provider_name: str, db_path: Path, inject_failure: str | None = None) -> int:
    load_env_file()
    settings = LLMSettings.from_env(provider=provider_name)
    script = None
    if inject_failure:
        if settings.provider != "mock":
            print("--inject-failure is only available with --provider mock", file=sys.stderr)
            return 2
        script = MockScript(by_tag={inject_failure: MockStep(MockBehavior.TIMEOUT)})
    try:
        provider, guard = build_provider(settings, mock_script=script, mock_responder=rules_responder)
    except LLMConfigurationError as exc:
        print(f"Live configuration incomplete, no request was made: {exc}", file=sys.stderr)
        return 2

    if db_path.exists():
        db_path.unlink()
    sim = SwarmSimulator(db_path, clock=SteppingClock())
    scenario, wf = DistributedApprovalScenario(), "wf_demo_001"
    run = await scenario.run(sim, wf)
    builder = sim.observation_builder()
    snapshot = builder.snapshot_for_action(run.critical_action_id or "")
    context = builder.ethics_context(snapshot, run.critical_action_id or "")
    council = build_specialized_council(provider, max_concurrency=settings.max_concurrency,
                                        max_output_tokens=settings.max_output_tokens,
                                        temperature=settings.temperature, seed=settings.seed,
                                        timeout_seconds=max(settings.timeout_seconds * (settings.max_retries + 1), 5))
    report = await council.deliberate(context.action, context)
    record = sim.monitor.record_council_evaluation(report, snapshot_digest=snapshot.digest())

    print(f"{BAR}\nSWARMGUARD — STAGE 3 ETHICS COUNCIL\n{BAR}")
    synthetic = provider.is_synthetic
    print(f"Provider: {provider.name}   model: {provider.model}   "
          + ("(SYNTHETIC MOCK: deterministic rules, NOT LLM reasoning)" if synthetic else "(live LLM)"))
    print(f"Prompt version: {prompt_manifest()['prompt_version']}   aggregation: {report.aggregation_method}")
    print(f"\nWorkflow:\n{wf}\n\nScenario:\nDistributed Approval Laundering")
    print(f"Evaluated action: {context.action.action_id} ({context.action.tool}) at checkpoint seq "
          f"{context.observation.checkpoint_seq if context.observation else '?'}; snapshot {snapshot.digest()[:16]}…")
    citable = set(context.observation.event_ids()) if context.observation else set()

    by_framework = {a.framework.value: a for a in report.assessments}
    failed = {f.framework.value: f for f in report.failures}
    for fw in SPECIALIZED_FRAMEWORKS:
        section(TITLES[fw.value])
        a = by_framework.get(fw.value)
        if a is None:
            f = failed.get(fw.value)
            print(f"FAILED ({f.error_kind if f else 'missing'}): {f.message if f else 'no result'}")
            continue
        print(f"Risk:\n{a.risk_level.value}\n\nRecommendation:\n{a.recommendation.value}\n\nUncertainty:\n{a.uncertainty.value}")
        print("\nConcerns:")
        for c in a.concerns or ["(none)"]:
            print(f"- {c}")
        ok = set(a.evidence_event_ids) <= citable
        print(f"\nEvidence: {', '.join(a.evidence_event_ids) or 'none'} (all in supplied context: {'yes' if ok else 'NO'})")
        if a.call and a.call.get("usage", {}).get("available"):
            u = a.call["usage"]
            print(f"Usage: {u['input_tokens']} in / {u['output_tokens']} out tokens, {a.call.get('latency_ms', 0):.0f} ms")

    section("COUNCIL SUMMARY")
    d = report.disagreement or {}
    print(f"Recommendation:\n{report.council_recommendation.value}  ({report.aggregation_method})")
    print(f"\nDisagreement:\n{'yes' if d.get('disagreement_detected') else 'no'} "
          f"({d.get('distinct_recommendations')} distinct recommendation(s); recommendations {d.get('recommendations')})")
    print(f"\nFailed Monitors:\n{len(report.failed_monitors)}" + (f" ({', '.join(report.failed_monitors)})"
                                                                 if report.failed_monitors else ""))

    section("DETERMINISTIC AUTHORIZATION")
    decision = sim.monitor.get_decision_for_action(run.critical_action_id or "")
    assert decision is not None
    print(f"Decision:\n{decision.decision.value} [{decision.reason_code.value}]  executed={decision.executed}")

    section("FINAL RESULT")
    print(f"Ethics Council:\n{report.council_recommendation.value} (advisory; observation mode)")
    print(f"\nCapability Gateway:\n{decision.decision.value} (the council did not and cannot change this decision)")
    print(f"\nProvider ledger: {guard.ledger.snapshot()}")
    stored = sim.monitor.get_council_evaluations(wf)
    print(f"Persisted council evaluation {record.evaluation_id} to {db_path} ({len(stored)} record(s)).")

    evidence_ok = all(set(a.evidence_event_ids) <= citable for a in report.assessments)
    ok = (len(report.assessments) + len(report.failures) == 4 and evidence_ok
          and {a.framework for a in report.assessments} | {f.framework for f in report.failures} == set(SPECIALIZED_FRAMEWORKS)
          and stored and stored[0].report == report)
    print(f"\n{BAR}\nStage 3 demonstration check: {'PASS' if ok else 'FAIL'} "
          "(infrastructure only, not which recommendation was made)\n" + BAR)
    sim.close()
    return 0 if ok else 1


if __name__ == "__main__":
    configure_logging(os.environ.get("SWARMGUARD_LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=("mock", "openai"), default="mock")
    parser.add_argument("--db", type=Path, default=Path("swarmguard_stage3_demo.db"))
    parser.add_argument("--inject-failure", choices=[f.value for f in SPECIALIZED_FRAMEWORKS], default=None,
                        help="(mock only) make one framework's provider call time out")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.provider, args.db, args.inject_failure)))
