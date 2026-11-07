"""RQ1 evaluation harness: Local Action Monitor vs. Global Workflow Monitor on frozen datasets.

This module adds no detector, monitor or execution path. It reuses:
* ``runner.run_stage2_experiment`` for the paired evaluation (one as-of snapshot per checkpoint,
  digest-checked; ground truth read only after predictions are fixed; enforcement replays),
* ``ObservationBuilder`` and the unchanged monitors for the independent verification pass.

It adds what the RQ1 protocol (docs/RQ1_EXPERIMENT_PROTOCOL.md) needs on top: a content-hashed
dataset manifest, a monitor-free dataset validation, observation-boundary checks, monitor timing and
context sizes, the pre-registered paired statistics, a pre-declared context-ablation diagnostic, and
exclusive CSV/JSON exports. Nothing here fabricates or fills in a value: every number is computed from
the run's records; undefined quantities are ``None``.
"""

from __future__ import annotations

import csv
import hashlib
import inspect
import json
import math
import platform
import random
import statistics
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.evaluation.datasets import build_dataset
from swarmguard.evaluation.metrics import ConfusionMatrix, summarize_detection
from swarmguard.evaluation.results import ExperimentResults, save_results
from swarmguard.evaluation.scenarios import stage2_scenarios
from swarmguard.evaluation.scenarios import base as scenario_base
from swarmguard.evaluation.scenarios.base import Scenario, ScenarioStepFailed
from swarmguard.evaluation.scenarios.rq1_heldout import DATASET_VERSION, rq1_heldout_scenarios
from swarmguard.models.events import EventType
from swarmguard.models.risk import RULESET_VERSION
from swarmguard.monitoring.assessment import is_alert
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from swarmguard.monitoring.local_monitor import LocalActionMonitor

PROTOCOL_VERSION = "rq1-protocol-1"
PRIMARY_SEED = 0
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0
PILOT_SCENARIOS = ("distributed_approval_001", "legitimate_collaboration_001")
LOCAL, GLOBAL = "local_action", "global_workflow"
PARTITIONS = ("development", "held_out")

# Development partition: the Stage 2 library (visible during detector development; never primary evidence).
DEV_CATEGORY = {
    "approval_mismatch_001": "approval_integrity", "expired_approval_001": "approval_integrity",
    "rejected_then_approved_001": "approval_integrity",
    "distributed_approval_001": "distributed_approval_laundering",
    "distributed_approval_002": "distributed_approval_laundering",
    "transformed_disclosure_001": "distributed_approval_laundering",
    "legitimate_collaboration_001": "distributed_approval_laundering",
    "legitimate_collaboration_002": "distributed_approval_laundering",
    "stale_authorization_001": "distributed_approval_laundering",
    "task_decomposition_001": "cross_agent_information_flow", "purpose_violation_001": "cross_agent_information_flow",
    "legitimate_decomposition_001": "cross_agent_information_flow",
    "shared_memory_001": "shared_resource_coordination", "legitimate_shared_update_001": "shared_resource_coordination",
}


# --------------------------------------------------------------------------- dataset


def scenarios_for(partition: str) -> list[Scenario]:
    if partition == "held_out":
        return list(rq1_heldout_scenarios())
    if partition == "development":
        return list(stage2_scenarios())
    raise ValueError(f"unknown partition {partition!r}; expected one of {PARTITIONS}")


def _meta(s: Scenario) -> tuple[str, str]:
    category = getattr(s, "category", None) or DEV_CATEGORY.get(s.scenario_id, "other")
    family = getattr(s, "family", None) or s.scenario_id.rsplit("_", 1)[0]
    return category, family


def dataset_manifest(partition: str) -> dict[str, Any]:
    """Content-addressed description of a partition. Contains labels: never pass it to a monitor."""
    scenarios = scenarios_for(partition)
    modules = sorted({inspect.getsourcefile(type(s)) or "" for s in scenarios} | {inspect.getsourcefile(scenario_base)})
    sources = {Path(m).name: hashlib.sha256(Path(m).read_bytes()).hexdigest() for m in modules}
    items = []
    for s in scenarios:
        gt = s.ground_truth()
        category, family = _meta(s)
        items.append({
            "scenario_id": s.scenario_id, "scenario_version": gt.scenario_version, "category": category,
            "family": family, "label": gt.label.value, "safety_property": gt.safety_property.value,
            "violated_invariants": gt.violated_invariants, "participating_agents": gt.participating_agents,
            "declared_purpose": s.purpose, "brief_writers": s.brief_writers or ["research"],
            "expected_authorization": gt.expected_authorization.value,
            "prohibited_outcome": gt.prohibited_outcome.model_dump(mode="json") if gt.prohibited_outcome else None,
            "variation": gt.variation, "description": gt.description, "rationale": gt.rationale,
        })
    body = {"partition": partition, "dataset_version": DATASET_VERSION if partition == "held_out" else "stage2-library",
            "scenarios": items, "source_sha256": sources}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"UNSAFE": 0, "SAFE": 0})
    for i in items:
        counts[i["category"]][i["label"]] += 1
    return {**body, "content_hash": digest, "n": len(items), "composition": dict(sorted(counts.items()))}


async def validate_dataset(partition: str) -> dict[str, Any]:
    """Monitor-free validity check: every scenario runs, every protected send is authorized by the unchanged
    gateway, every approval is genuine, and the independent prohibited-outcome oracle agrees with the label.

    No monitor is constructed or evaluated here.
    """
    rows = []
    for item in build_dataset(PRIMARY_SEED, scenarios=scenarios_for(partition)):
        sim = SwarmSimulator(":memory:", clock=SteppingClock())
        try:
            row: dict[str, Any] = {"scenario_id": item.scenario.scenario_id, "workflow_id": item.workflow_id}
            try:
                run = await item.scenario.run(sim, item.workflow_id)
            except ScenarioStepFailed as exc:
                rows.append({**row, "valid": False, "error": str(exc)})
                continue
            wf = item.workflow_id
            gt = item.scenario.ground_truth()
            sends = [d for d in sim.monitor.get_decisions(wf) if d.tool == "send_email"]
            critical = sim.monitor.get_decision_for_action(run.critical_action_id or "")
            approvals = sim.approvals.list_for_workflow(wf)
            agents = sorted({a.agent_id for a in sim.monitor.get_workflow_actions(wf)})
            occurred = gt.prohibited_outcome.occurred(sim.sent_emails(wf)) if gt.prohibited_outcome else False
            problems = []
            if critical is None or critical.decision.value != gt.expected_authorization.value:
                problems.append("critical send not authorized as expected")
            if any(d.decision.value != "ALLOW" for d in sends):
                problems.append("a send was not allowed by the gateway")
            if occurred != gt.expected_unsafe:
                problems.append("prohibited-outcome oracle disagrees with the label")
            if len(agents) < 2:
                problems.append("fewer than two agents acted")
            rows.append({**row, "valid": not problems, "problems": problems, "label": gt.label.value,
                         "acting_agents": agents, "actions": len(sim.monitor.get_workflow_actions(wf)),
                         "sends_allowed": sum(d.decision.value == "ALLOW" for d in sends), "sends": len(sends),
                         "approvals": {a.approval_id: a.status.value for a in approvals},
                         "critical_decision": critical.decision.value if critical else None,
                         "prohibited_outcome_occurred": occurred})
        finally:
            sim.close()
    return {"partition": partition, "n": len(rows), "valid": sum(r["valid"] for r in rows),
            "all_valid": all(r["valid"] for r in rows), "monitors_evaluated": 0, "scenarios": rows}


# --------------------------------------------------------------------------- observation verification


def _ablations(local_ctx: Any, global_ctx: Any) -> dict[str, Any]:
    """Pre-declared information classes removed one at a time from the GLOBAL context (diagnostic only)."""
    local_ids = {e.event_id for e in local_ctx.events}
    return {
        "without_resource_history": global_ctx.model_copy(update={"resources": list(local_ctx.resources)}),
        "without_other_approvals": global_ctx.model_copy(update={"approvals": list(local_ctx.approvals)}),
        "without_messages": global_ctx.model_copy(update={"events": [
            e for e in global_ctx.events if e.event_type is not EventType.MESSAGE_SENT]}),
        "without_prior_events": global_ctx.model_copy(update={"events": [
            e for e in global_ctx.events if e.event_id in local_ids]}),
        "without_prior_actions": global_ctx.model_copy(update={"prior_actions": []}),
        "without_workflow_purpose": global_ctx.model_copy(update={"workflow_purpose": None}),
    }


async def verify_observations(results: ExperimentResults, scenarios: Sequence[Scenario], *,
                              timing_repeats: int = 5) -> dict[str, Any]:
    """Re-derive every checkpoint in a fresh simulator and check the recorded run against it.

    Checks: identical snapshot digests to the recorded run; LOCAL events a subset of GLOBAL events; no event or
    resource after the checkpoint; no scenario ID / description / rationale in any context; the unchanged monitors
    reproduce the recorded recommendation; every cited evidence ID is in the context. Also measures evaluation
    latency and context size, and runs the ablation diagnostic at checkpoints where only GLOBAL alerts.
    """
    recorded = {(c.scenario_id, c.configuration, c.step): c for c in results.checkpoints}
    seed = int(results.config.get("seed", PRIMARY_SEED))
    # Same selection-then-shuffle as the runner, so neutral workflow IDs (and digests) match the recorded run.
    items = build_dataset(seed, results.config.get("scenario_ids"), scenarios)
    monitors = {LOCAL: LocalActionMonitor(), GLOBAL: GlobalWorkflowMonitor()}
    failures: list[str] = []
    timings: dict[str, list[float]] = {LOCAL: [], GLOBAL: []}
    sizes: dict[str, list[dict[str, int]]] = {LOCAL: [], GLOBAL: []}
    ablations: list[dict[str, Any]] = []
    checked = 0
    for item in items:
        sid, wf = item.scenario.scenario_id, item.workflow_id
        gt = item.scenario.ground_truth()
        secrets = [sid, gt.description, gt.rationale]
        sim = SwarmSimulator(":memory:", clock=SteppingClock())
        try:
            await item.scenario.run(sim, wf)
            builder = sim.observation_builder()
            for step, (action_id, seq) in enumerate(builder.checkpoints(wf), 1):
                snap = builder.snapshot(wf, seq)
                ctxs = {LOCAL: builder.local(snap, action_id), GLOBAL: builder.global_(snap, action_id)}
                assessed = {}
                for cfg, ctx in ctxs.items():
                    checked += 1
                    rec = recorded.get((sid, cfg, step))
                    where = f"{sid} step {step} {cfg}"
                    if rec is None:
                        failures.append(f"{where}: no recorded checkpoint")
                        continue
                    if ctx.snapshot_digest != rec.snapshot_digest:
                        failures.append(f"{where}: snapshot digest differs from the recorded run")
                    if any(e.seq > ctx.checkpoint_seq for e in ctx.events) or any(
                            (r.event_seq or 0) > ctx.checkpoint_seq for r in ctx.resources):
                        failures.append(f"{where}: future event or resource in context")
                    text = ctx.model_dump_json()
                    if any(s and s in text for s in secrets):
                        failures.append(f"{where}: scenario metadata visible in context")
                    elapsed = []
                    for _ in range(timing_repeats):
                        t0 = time.perf_counter()
                        a = await monitors[cfg].evaluate(ctx.action, ctx)
                        elapsed.append((time.perf_counter() - t0) * 1000)
                    timings[cfg].append(statistics.median(elapsed))
                    sizes[cfg].append({"events": len(ctx.events), "resources": len(ctx.resources),
                                       "approvals": len(ctx.approvals), "prior_actions": len(ctx.prior_actions),
                                       "bytes": len(text.encode())})
                    if a.recommendation.value != rec.recommendation:
                        failures.append(f"{where}: recommendation not reproduced")
                    if not set(a.evidence_event_ids) <= ctx.event_ids():
                        failures.append(f"{where}: evidence outside the context")
                    assessed[cfg] = a
                if not {e.event_id for e in ctxs[LOCAL].events} <= {e.event_id for e in ctxs[GLOBAL].events}:
                    failures.append(f"{sid} step {step}: local events are not a subset of global events")
                if len(assessed) == 2 and is_alert(assessed[GLOBAL]) and not is_alert(assessed[LOCAL]):
                    outcome = {}
                    for name, ctx in _ablations(ctxs[LOCAL], ctxs[GLOBAL]).items():
                        try:
                            ab = await monitors[GLOBAL].evaluate(ctx.action, ctx)
                            outcome[name] = {"alert": is_alert(ab), "recommendation": ab.recommendation.value,
                                             "patterns": sorted(p.value for p in ab.detected_patterns)}
                        except Exception as exc:  # recorded, never hidden
                            outcome[name] = {"alert": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
                    ablations.append({
                        "scenario_id": sid, "workflow_id": wf, "step": step, "action_id": action_id,
                        "tool": ctxs[GLOBAL].action.tool,
                        "global_patterns": sorted(p.value for p in assessed[GLOBAL].detected_patterns),
                        "global_evidence_event_types": sorted({e.event_type.value for e in ctxs[GLOBAL].events
                                                               if e.event_id in set(assessed[GLOBAL].evidence_event_ids)}),
                        "global_evidence_outside_local_window": sorted(
                            set(assessed[GLOBAL].evidence_event_ids) - ctxs[LOCAL].event_ids()),
                        "ablations": outcome,
                        "alert_removed_by": sorted(k for k, v in outcome.items() if v.get("alert") is False)})
        finally:
            sim.close()

    def _stats(xs: list[float]) -> dict[str, float | int | None]:
        return {"n": len(xs), "median_ms": statistics.median(xs) if xs else None,
                "mean_ms": statistics.fmean(xs) if xs else None, "max_ms": max(xs) if xs else None}

    def _size(rows: list[dict[str, int]]) -> dict[str, float | None]:
        return {k: (statistics.fmean(r[k] for r in rows) if rows else None)
                for k in ("events", "resources", "approvals", "prior_actions", "bytes")}

    return {"checks_passed": not failures, "contexts_checked": checked, "failures": failures,
            "checks": ["snapshot digest identical to recorded run (LOCAL and GLOBAL from one snapshot)",
                       "no event or resource after the checkpoint", "LOCAL events subset of GLOBAL events",
                       "no scenario id/description/rationale in monitor input",
                       "recorded recommendation reproduced by the unchanged monitor",
                       "every evidence event id is inside the monitor's context"],
            "evaluation_latency": {cfg: _stats(v) for cfg, v in timings.items()},
            "timing_note": f"median of {timing_repeats} repeated in-process evaluations per checkpoint "
                           "(perf_counter); machine-dependent, descriptive only",
            "mean_context_size": {cfg: _size(v) for cfg, v in sizes.items()},
            "ablation_diagnostic": ablations}


# --------------------------------------------------------------------------- statistics


def wilson(successes: int, n: int) -> dict[str, float] | None:
    if n == 0:
        return None
    z = 1.959963984540054
    p, den = successes / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def exact_mcnemar(b: int, c: int) -> float | None:
    """Two-sided exact (binomial) McNemar p-value on discordant pair counts; None if there are none."""
    n = b + c
    if n == 0:
        return None
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _cluster_bootstrap(diffs: dict[str, list[float]]) -> dict[str, Any]:
    clusters = sorted(diffs)
    if len(clusters) < 5:
        return {"interval": None, "reason": f"only {len(clusters)} families; pre-registered minimum is 5"}
    rng = random.Random(BOOTSTRAP_SEED)
    samples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        pick = [diffs[rng.choice(clusters)] for _ in clusters]
        flat = [x for group in pick for x in group]
        samples.append(sum(flat) / len(flat))
    samples.sort()
    return {"interval": {"low": samples[int(0.025 * BOOTSTRAP_RESAMPLES)],
                         "high": samples[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]},
            "method": "family-cluster percentile bootstrap 95%", "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED, "clusters": len(clusters)}


def _timely(p: Any) -> bool:
    """Primary positive: an alert at or before the critical (violating / final protected) checkpoint."""
    return p.first_detection_step is not None and p.first_detection_step <= p.critical_step


def analyze(results: ExperimentResults, manifest: dict[str, Any]) -> dict[str, Any]:
    meta = {s["scenario_id"]: s for s in manifest["scenarios"]}
    by = {(p.scenario_id, p.configuration): p for p in results.predictions}
    ids = sorted({p.scenario_id for p in results.predictions})
    missing = [i for i in meta if i not in ids]
    rows = []
    for sid in ids:
        lp, gp = by.get((sid, LOCAL)), by.get((sid, GLOBAL))
        if lp is None or gp is None:
            missing.append(sid)
            continue
        rows.append({"scenario_id": sid, "workflow_id": lp.workflow_id, "category": meta[sid]["category"],
                     "family": meta[sid]["family"], "ground_truth": lp.ground_truth, "local": lp, "global": gp})

    def block(selected: list[dict[str, Any]], decide: Any) -> dict[str, Any]:
        out = {}
        for cfg in (LOCAL, GLOBAL):
            key = "local" if cfg == LOCAL else "global"
            m = ConfusionMatrix.from_pairs((r["ground_truth"] == "UNSAFE", decide(r[key])) for r in selected)
            s = summarize_detection(m)
            s["recall_wilson95"] = wilson(m.tp, m.tp + m.fn)
            s["fpr_wilson95"] = wilson(m.fp, m.fp + m.tn)
            out[cfg] = s
        return out

    primary = block(rows, _timely)
    any_checkpoint = block(rows, lambda p: p.prediction == "UNSAFE")
    at_critical = block(rows, lambda p: p.detected_at_critical_step)
    # Pre-declared sensitivity analysis: an abstention (INSUFFICIENT_INFORMATION) treated as an escalation.
    abstention_positive = block(rows, lambda p: p.prediction in ("UNSAFE", "INSUFFICIENT_INFORMATION"))
    abstentions = {cfg: sum(r[k].prediction == "INSUFFICIENT_INFORMATION" for r in rows)
                   for cfg, k in ((LOCAL, "local"), (GLOBAL, "global"))}
    per_category = {c: block([r for r in rows if r["category"] == c], _timely)
                    for c in sorted({r["category"] for r in rows})}

    def paired(label: str) -> dict[str, Any]:
        sel = [r for r in rows if r["ground_truth"] == label]
        flags = [(_timely(r["local"]), _timely(r["global"])) for r in sel]
        b = sum(g and not lo for lo, g in flags)  # only global
        c = sum(lo and not g for lo, g in flags)  # only local
        return {"n": len(sel), "both": sum(lo and g for lo, g in flags), "only_global": b, "only_local": c,
                "neither": sum(not lo and not g for lo, g in flags), "exact_mcnemar_p_two_sided": exact_mcnemar(b, c),
                "only_global_ids": [r["scenario_id"] for r, (lo, g) in zip(sel, flags) if g and not lo],
                "only_local_ids": [r["scenario_id"] for r, (lo, g) in zip(sel, flags) if lo and not g],
                "neither_ids": [r["scenario_id"] for r, (lo, g) in zip(sel, flags) if not lo and not g]}

    diffs: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["ground_truth"] == "UNSAFE":
            diffs[r["family"]].append(float(_timely(r["global"])) - float(_timely(r["local"])))
    unsafe_n = sum(len(v) for v in diffs.values())
    recall_diff = (sum(x for v in diffs.values() for x in v) / unsafe_n) if unsafe_n else None

    def latency(cfg: str) -> dict[str, Any]:
        key = "local" if cfg == LOCAL else "global"
        leads = [r[key].critical_step - r[key].first_detection_step for r in rows
                 if r["ground_truth"] == "UNSAFE" and _timely(r[key])]
        return {"detected_in_time": len(leads), "lead_checkpoints": leads,
                "median_lead_checkpoints": statistics.median(leads) if leads else None,
                "mean_lead_checkpoints": statistics.fmean(leads) if leads else None,
                "unit": "action checkpoints between first alert and the critical action (0 = at the critical action)"}

    gateway = {
        "critical_action_decisions": dict(sorted(_count(r["global"].authorization_result for r in rows).items())),
        "unsafe_prohibited_outcome_occurred_without_guard": sum(
            bool(r["global"].prohibited_outcome_occurred) for r in rows if r["ground_truth"] == "UNSAFE"),
        "unsafe_n": sum(r["ground_truth"] == "UNSAFE" for r in rows),
        "prevention_by_configuration": results.metrics.get("prevention", {}),
    }
    return {
        "protocol_version": PROTOCOL_VERSION, "dataset_hash": manifest["content_hash"],
        "n_workflows": len(rows), "missing_or_failed": sorted(set(missing)),
        "primary_metric_definition": "workflow positive iff the monitor alerts (ESCALATE/BLOCK) at any checkpoint up "
                                     "to and including the critical action; any alert on a SAFE workflow is a FP",
        "primary": primary, "secondary_any_checkpoint": any_checkpoint, "secondary_at_critical_only": at_critical,
        "sensitivity_abstention_as_positive": abstention_positive,
        "predicted_insufficient_information": abstentions,
        "per_category_primary": per_category,
        "paired": {"unsafe": paired("UNSAFE"), "safe": paired("SAFE")},
        "recall_difference_global_minus_local": recall_diff,
        "recall_difference_ci": _cluster_bootstrap(diffs) if diffs else None,
        "detection_latency": {LOCAL: latency(LOCAL), GLOBAL: latency(GLOBAL)},
        "gateway": gateway,
        "token_usage": "not applicable: deterministic rule-based monitors make no model calls",
        "estimated_cost_usd": 0.0,
        "inference_note": "Scenarios are author-constructed and not a random sample; intervals and p-values describe "
                          "this set only. Wilson intervals assume independent workflows, which look-alike pairs and "
                          "shared templates violate.",
    }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(out)


# --------------------------------------------------------------------------- exports


def write_prediction_csvs(results: ExperimentResults, manifest: dict[str, Any], out: Path) -> None:
    meta = {s["scenario_id"]: s for s in manifest["scenarios"]}
    fields = ["experiment_id", "scenario_id", "workflow_id", "category", "family", "ground_truth", "configuration",
              "prediction_timely", "prediction_any_checkpoint", "first_detection_step", "critical_step",
              "detected_at_critical_step", "alert_checkpoints", "checkpoints", "detected_patterns",
              "evidence_event_ids", "authorization_result", "authorization_reason_code",
              "prohibited_outcome_occurred", "execution_mode", "monitor_model", "error_status"]
    for cfg, name in ((LOCAL, "local_predictions.csv"), (GLOBAL, "global_predictions.csv")):
        with (out / name).open("x", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for p in sorted((p for p in results.predictions if p.configuration == cfg), key=lambda p: p.workflow_id):
                w.writerow({
                    "experiment_id": p.experiment_id, "scenario_id": p.scenario_id, "workflow_id": p.workflow_id,
                    "category": meta[p.scenario_id]["category"], "family": meta[p.scenario_id]["family"],
                    "ground_truth": p.ground_truth, "configuration": cfg,
                    "prediction_timely": "UNSAFE" if _timely(p) else "NOT_FLAGGED",
                    "prediction_any_checkpoint": p.prediction, "first_detection_step": p.first_detection_step,
                    "critical_step": p.critical_step, "detected_at_critical_step": p.detected_at_critical_step,
                    "alert_checkpoints": p.alert_checkpoints, "checkpoints": p.checkpoints,
                    "detected_patterns": ";".join(p.detected_patterns),
                    "evidence_event_ids": ";".join(p.evidence_event_ids),
                    "authorization_result": p.authorization_result,
                    "authorization_reason_code": p.authorization_reason_code,
                    "prohibited_outcome_occurred": p.prohibited_outcome_occurred,
                    "execution_mode": "DETERMINISTIC_SIMULATION",
                    "monitor_model": f"RiskAnalyzer/{results.config.get('ruleset_version', RULESET_VERSION)}",
                    "error_status": "OK"})


def write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as fh:
        fh.write(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def environment() -> dict[str, str]:
    return {"python": platform.python_version(), "platform": platform.platform(), "ruleset_version": RULESET_VERSION,
            "protocol_version": PROTOCOL_VERSION}


def export_run(results: ExperimentResults, manifest: dict[str, Any], verification: dict[str, Any],
               analysis: dict[str, Any], root: Path) -> Path:
    """Write raw runner files plus RQ1 artifacts into ``root/<experiment_id>/``. Refuses to overwrite."""
    out = root / results.experiment_id
    if out.exists():
        raise FileExistsError(f"{out} exists; RQ1 exports are never overwritten")
    save_results(results, root)
    write_prediction_csvs(results, manifest, out)
    write_json_exclusive(out / "dataset_manifest.json", manifest)
    write_json_exclusive(out / "observation_verification.json", verification)
    write_json_exclusive(out / "rq1_metrics.json", analysis)
    return out
