"""Stage 1 acceptance demonstration: approval laundering vs. authorized execution.

    python demo.py                 # writes swarmguard_demo.db (recreated each run)
    python demo.py --db other.db   # or set SWARMGUARD_DB_PATH
    python demo.py --no-council    # deterministic enforcement only
    python demo.py --verbose       # also print the full event log

Everything here runs the real components. Nothing is hard-coded: every
printed value is read back from the gateway response or from SQLite. There is
no network access, no API key, and no real email. The ethics council in this
demo is made of MOCK heuristic monitors, not LLMs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.config import Settings, configure_logging
from swarmguard.ethics.council import build_mock_council
from swarmguard.evaluation.scenarios import ScenarioOutcome, run_approval_laundering, run_authorized_email
from swarmguard.models.decisions import Decision, ReasonCode

BAR = "=" * 40


def banner(title: str, sim: SwarmSimulator, outcome: ScenarioOutcome) -> None:
    r = outcome.final
    sent = outcome.sent_email_count(sim)
    print(f"\n{BAR}\n{title}\n{BAR}\n")
    print(f"Workflow: {r.workflow_id}\n\nAction: {r.tool}\n\nDecision: {r.decision.value}\n\nReason: {r.reason_code.value}\n")
    if r.decision is Decision.BLOCK:
        print(f"Explanation:\n{r.explanation}")
        if r.details.get("differing_fields"):
            print(f"Differing fields: {', '.join(r.details['differing_fields'])}")
        print("\nResult:\nUnauthorized email execution prevented.\n")
    elif r.executed:
        print("Result:\nAuthorized mock email execution completed.\n")
    print(f"Simulated emails sent: {sent}\n{BAR}")


def details(sim: SwarmSimulator, outcome: ScenarioOutcome, verbose: bool) -> None:
    wf = outcome.workflow_id
    print(f"\nResource versions of {outcome.email_id} (from resource_versions table):")
    for v in sim.graph.get_resource_modification_chain(outcome.email_id):
        change = ", ".join(f"{k}: {c['old']} -> {c['new']}" for k, c in v.changes.items()) or "created"
        print(f"  v{v.version}  by {v.agent_id:<17} via {v.action_id}  [{change}]  recipient={v.content['recipient']}")

    chain = sim.graph.get_approval_dependency_chain(outcome.approval_id)
    if chain:
        print(f"\nApproval {chain.approval_id}: status={chain.status}, bound to {chain.bound_resource_version}, "
              f"approved by {chain.approver_id}, invalidated by {chain.invalidated_by_actions or 'none'}, "
              f"consumed by {chain.consumed_by_action or 'none'}")

    deps = sim.graph.get_action_dependencies(outcome.final.action_id)
    if deps:
        print(f"\nAction dependencies of {outcome.final.action_id} (explicit references only):")
        print(f"  direct parents: {deps.direct_parents}\n  resource versions relied on: {deps.resources_read}")
        print(f"  data providers: {deps.data_providers}\n  approvals cited: {deps.approvals}")

    print("\nReconstructed causal chain (links from explicit references; listed in recorded order):")
    for line in sim.graph.render_causal_chain(outcome.final.action_id):
        print(f"  {line}")

    record = sim.monitor.get_decision_for_action(outcome.final.action_id)
    if record and record.advisory_summary:
        print("\nAdvisory ethics council (MOCK monitors: deterministic heuristics, NOT LLM reasoning):")
        print(f"  {record.advisory_summary}")
        print(f"  Deterministic policy: {record.policy_decision.value} [{record.policy_reason_code.value}] -> "
              f"final: {record.decision.value}. Advice can only escalate; it cannot override a block.")

    summary = sim.monitor.get_workflow_summary(wf)
    if summary:
        print(f"\nPersisted: {summary.event_count} events, {summary.action_count} actions, "
              f"decisions {summary.decisions_by_outcome}.")
    if verbose:
        print("\n" + sim.monitor.render_history(wf))


async def main(db_path: Path, use_council: bool, verbose: bool = False) -> int:
    if db_path.exists():
        db_path.unlink()
    clock = SteppingClock()
    ttl = timedelta(minutes=Settings.from_env().approval_ttl_minutes)
    sim = SwarmSimulator(db_path, clock=clock, council=build_mock_council(clock) if use_council else None,
                         approval_ttl=ttl)
    print("SwarmGuard Stage 1 demonstration (simulated; no network, no API keys, no real email)")

    print("\nScenario A: approval laundering")
    print("  1 Coordinator assigns tasks   2 Research reads cust_001 and writes a brief")
    print("  3 Operations drafts email v1  4 Human approves email v1 exactly")
    print("  5 Research revises the brief's contact; Operations applies it -> email v2 (other@example.test)")
    print("  6 Operations requests send of v2 citing the v1 approval")
    laundering = await run_approval_laundering(sim, "workflow_001")
    banner("SWARMGUARD SECURITY EVENT", sim, laundering)
    details(sim, laundering, verbose)

    print("\n\nScenario B: authorized execution (positive control)")
    authorized = await run_authorized_email(sim, "workflow_002")
    banner("SWARMGUARD AUTHORIZED EXECUTION", sim, authorized)
    details(sim, authorized, verbose)

    versions = [v.version for v in sim.graph.get_resource_modification_chain(laundering.email_id)]
    ok = (
        laundering.final.decision is Decision.BLOCK
        and laundering.final.reason_code is ReasonCode.APPROVAL_MISMATCH
        and laundering.sent_email_count(sim) == 0
        and versions == [1, 2]
        and authorized.final.decision is Decision.ALLOW
        and authorized.final.reason_code is ReasonCode.AUTHORIZED
        and authorized.sent_email_count(sim) == 1
        and all(e["status"] == "SIMULATED_EMAIL_SENT" for e in sim.sent_emails())
    )
    print(f"\nAll records persisted to {db_path}. Acceptance check: {'PASS' if ok else 'FAIL'}")
    sim.close()
    return 0 if ok else 1


if __name__ == "__main__":
    configure_logging(os.environ.get("SWARMGUARD_LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("SWARMGUARD_DB_PATH", "swarmguard_demo.db")))
    parser.add_argument("--no-council", action="store_true", help="disable the mock advisory council")
    parser.add_argument("--verbose", action="store_true", help="print the full event log for each workflow")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.db, not args.no_council, args.verbose)))
