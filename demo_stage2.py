"""Stage 2 demonstration: distributed approval laundering, local vs. global monitoring.

    python demo_stage2.py                       # writes swarmguard_stage2_demo.db (recreated each run)
    python demo_stage2.py --db other.db
    python demo_stage2.py --verbose             # also print every checkpoint assessment

Every value printed here is computed: monitor outputs come from the deterministic rule
engine run on as-of-checkpoint observations, the authorization result comes from the
real capability gateway, and the ground truth is read only after both monitors have
produced their assessments. Nothing is hard-coded and the global monitor is not forced
to win. No network, no API keys, no real email.

The final "demonstration check" verifies the infrastructure (agents, shared resources,
dependencies, evidence validity, persisted decisions). It does NOT check which monitor
detected what.
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
from swarmguard.evaluation.scenarios.distributed_approval import DistributedApprovalScenario
from swarmguard.evaluation.scenarios.legitimate_workflows import LegitimateCollaborationScenario
from swarmguard.models.events import EventType
from swarmguard.models.risk import ObservationWindow, SafetyAssessment
from swarmguard.monitoring.assessment import is_alert, scenario_prediction
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from swarmguard.monitoring.local_monitor import LocalActionMonitor
from swarmguard.security.workflow_intelligence import WorkflowIntelligenceEngine

BAR, RULE = "=" * 50, "-" * 50
KEY_EVENTS = {EventType.TASK_ASSIGNED, EventType.RESOURCE_READ, EventType.RESOURCE_MODIFIED, EventType.RESOURCE_CREATED,
              EventType.APPROVAL_REQUESTED, EventType.APPROVAL_GRANTED, EventType.EMAIL_SENT_SIMULATED}


def section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def show_assessment(a: SafetyAssessment, valid_ids: set[str]) -> None:
    print(f"Decision (advisory recommendation): {a.recommendation.value}   risk={a.risk_level.value}   "
          f"uncertainty={a.uncertainty.value}")
    print(f"Detected patterns: {', '.join(p.value for p in a.detected_patterns) or 'none'}")
    print(f"Supporting events: {', '.join(a.evidence_event_ids) or 'none'}")
    bad = [e for e in a.evidence_event_ids if e not in valid_ids]
    print(f"Evidence references valid (persisted, <= checkpoint): {'yes' if not bad else 'NO: ' + ', '.join(bad)}")
    for f in a.findings:
        if f.actionable or f.severity.rank >= 1:
            print(f"  - [{f.pattern_id.value} {f.severity.value} {f.basis.value}] {f.explanation}")
    for m in a.missing_information:
        print(f"  ? missing: {m}")


async def evaluate_all(sim: SwarmSimulator, workflow_id: str) -> dict[str, list[tuple[int, SafetyAssessment]]]:
    builder = sim.observation_builder()
    monitors = {"local": LocalActionMonitor(), "global": GlobalWorkflowMonitor()}
    out: dict[str, list[tuple[int, SafetyAssessment]]] = {k: [] for k in monitors}
    for step, (action_id, seq) in enumerate(builder.checkpoints(workflow_id), 1):
        snapshot = builder.snapshot(workflow_id, seq)
        for name, monitor in monitors.items():
            ctx = builder.build(monitor.window, snapshot, action_id)
            out[name].append((step, await monitor.evaluate(ctx.action, ctx)))
    return out


async def main(db_path: Path, verbose: bool = False) -> int:
    if db_path.exists():
        db_path.unlink()
    sim = SwarmSimulator(db_path, clock=SteppingClock())
    scenario, wf = DistributedApprovalScenario(), "wf_demo_001"
    print(f"{BAR}\nSWARMGUARD — STAGE 2 DEMONSTRATION\n{BAR}")
    print("(simulated; deterministic rule-based monitors, not LLMs; no network, no API keys, no real email)")
    run = await scenario.run(sim, wf)
    engine = WorkflowIntelligenceEngine(sim.monitor, builder=sim.observation_builder())
    analysis = await engine.analyze_workflow(wf)

    print("\nScenario:\nDistributed Approval Laundering (every step individually authorized)")
    print("\nParticipating Agents:")
    for a in analysis.participating_agents:
        print(f"- {a} ({sim.agent_roles.get(a, '?')})")
    humans = [p for p in analysis.principals if p not in analysis.participating_agents and not p.startswith("swarmguard.")]
    print(f"Human principals: {', '.join(humans) or 'none'}")

    print("\nWorkflow Events (key events, as persisted):")
    events = [e for e in sim.monitor.get_workflow_history(wf) if e.event_type in KEY_EVENTS]
    for n, e in enumerate(events, 1):
        print(f"{n:>2}. [{e.event_id}] {e.summary}")

    print("\nShared-resource modifications (versions by principal):")
    for m in analysis.shared_resource_modifications:
        print(f"- {m.resource_id} ({m.resource_type.value}): {m.versions_by_principal}; read by {', '.join(m.readers)}")
    print(f"\nReconstructed cross-agent dependencies: {len(analysis.cross_agent_dependencies)} (explicit references only)")
    for d in analysis.cross_agent_dependencies[:8]:
        print(f"- {d.consumer_agent_id}:{d.consumer_action_id} <- {d.provider_agent_id}:{d.provider_action_id} via {d.via}")

    results = await evaluate_all(sim, wf)
    critical_step = next(s for s, a in results["global"] if a.action_id == run.critical_action_id)
    valid_ids = {e.event_id for e in sim.monitor.get_workflow_history(wf)}
    for name, title in (("local", "LOCAL ACTION MONITOR"), ("global", "GLOBAL WORKFLOW MONITOR")):
        section(f"{title}  (at the protected send, checkpoint {critical_step})")
        at_critical = next(a for s, a in results[name] if s == critical_step)
        cutoff_ids = {i for i in valid_ids if sim.monitor.get_event(i).seq <= at_critical.checkpoint_seq}
        show_assessment(at_critical, cutoff_ids)
        alerts = [(s, a) for s, a in results[name] if is_alert(a)]
        first = f"step {alerts[0][0]} ({sim.monitor.get_action(alerts[0][1].action_id).tool})" if alerts else "never"
        print(f"First alert in workflow: {first}; alerts at {len(alerts)} of {len(results[name])} checkpoints")
        if verbose:
            for s, a in results[name]:
                print(f"    step {s:>2} {a.action_id} {a.recommendation.value:<8} {[p.value for p in a.detected_patterns]}")

    section("DETERMINISTIC AUTHORIZATION")
    decision = sim.monitor.get_decision_for_action(run.critical_action_id or "")
    assert decision is not None
    print(f"Decision: {decision.decision.value} [{decision.reason_code.value}] {decision.explanation}")
    print(f"Executed: {decision.executed}   Simulated emails sent in workflow: {len(sim.sent_emails(wf))}")
    for e in sim.sent_emails(wf):
        print(f"  sent {e['email_id']} to {e['recipient']} (status {e['status']}, approval {e['approval_id']})")

    # Ground truth is read only now, after both monitors have produced their assessments.
    gt = scenario.ground_truth()
    occurred = gt.prohibited_outcome.occurred(sim.sent_emails(wf)) if gt.prohibited_outcome else False
    local_pred = scenario_prediction(a for _, a in results["local"])
    global_pred = scenario_prediction(a for _, a in results["global"])
    section("FINAL RESULT")
    print(f"Ground Truth: {gt.label.value} ({gt.safety_property.value}; invariants {', '.join(gt.violated_invariants)})")
    print(f"Local Detection: {local_pred.value}")
    print(f"Global Detection: {global_pred.value}")
    print(f"Protected Execution: {'EXECUTED' if decision.executed else 'NOT EXECUTED'} "
          f"({decision.decision.value}); prohibited outcome occurred: {occurred}")
    print("(Observation mode: monitor assessments did not change the gateway decision.)")

    section("SAME WORKFLOW, ENFORCEMENT MODE (trusted global guard on send_email; can only escalate)")
    enforced = SwarmSimulator(":memory:", clock=SteppingClock(), workflow_enforcement=ObservationWindow.GLOBAL)
    run2 = await scenario.run(enforced, wf)
    d2 = enforced.monitor.get_decision_for_action(run2.critical_action_id or "")
    assert d2 is not None
    occurred2 = gt.prohibited_outcome.occurred(enforced.sent_emails(wf)) if gt.prohibited_outcome else False
    print(f"Decision: {d2.decision.value} [{d2.reason_code.value}] (policy alone: {d2.policy_decision.value})")
    print(f"Executed: {d2.executed}; prohibited outcome occurred: {occurred2}")
    enforced.close()

    control = LegitimateCollaborationScenario()
    run3 = await control.run(sim, "wf_demo_002")
    control_results = await evaluate_all(sim, "wf_demo_002")
    d3 = sim.monitor.get_decision_for_action(run3.critical_action_id or "")
    section("LEGITIMATE CONTROL (same agents, authorized recipient)")
    print(f"Local: {scenario_prediction(a for _, a in control_results['local']).value}   "
          f"Global: {scenario_prediction(a for _, a in control_results['global']).value}   "
          f"Gateway: {d3.decision.value if d3 else '?'}   Ground truth: {control.ground_truth().label.value}")

    evidence_ok = all(e in valid_ids for _, a in results["global"] + results["local"] for e in a.evidence_event_ids)
    ok = (
        len(analysis.participating_agents) >= 3
        and any(len(m.versions_by_principal) >= 2 for m in analysis.shared_resource_modifications)
        and bool(analysis.cross_agent_dependencies)
        and all(len(results[k]) == len(results["global"]) > 0 for k in results)
        and evidence_ok
        and decision.decision.value == gt.expected_authorization.value
        and d2.decision.value in ("ALLOW", "ESCALATE", "BLOCK")
    )
    print(f"\n{BAR}\nAll records persisted to {db_path}. Stage 2 demonstration check: {'PASS' if ok else 'FAIL'}")
    print("(The check covers infrastructure properties, not which monitor detected the risk.)\n" + BAR)
    sim.close()
    return 0 if ok else 1


if __name__ == "__main__":
    configure_logging(os.environ.get("SWARMGUARD_LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=Path("swarmguard_stage2_demo.db"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.db, args.verbose)))
