"""Validate recorded evidence and recompute metrics without changing historical artifacts."""
from __future__ import annotations
from typing import Any
from swarmguard.models.risk import MonitorContext
from swarmguard.monitoring.evidence_validator import validate_evidence
from swarmguard.research.reproducibility import digest
from swarmguard.research.statistical_analysis import analyze


def validate_result(record: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    rows = record["rows"]
    ds = record["dataset"]
    specs = {s["scenario_id"]: s for s in ds["definition"]["scenarios"]}
    planned = len(specs)*record["config"]["trials"]
    seen = set()
    payload = {k: ds[k] for k in ("definition", "truth_hashes", "scenario_source_hash", "parent_hash")}
    if digest(payload) != ds["content_hash"] or record["manifest"]["dataset_hash"] != ds["content_hash"]:
        errors.append("dataset hash mismatch")
    for row in rows:
        tid = row.get("trial_id")
        sid = row.get("dataset_scenario_id")
        if not tid or tid in seen:
            errors.append("missing or duplicate trial identifier")
        seen.add(tid)
        if sid not in specs:
            errors.append("unknown scenario")
            continue
        if row.get("evaluation_valid"):
            truth = row.get("ground_truth")
            if not truth or digest(truth) != ds["truth_hashes"][sid]:
                errors.append("trusted scoring specification mismatch")
            if not row.get("actions") or not row.get("decisions"):
                errors.append("missing action or authorization evidence")
            for name, value in row.get("predictions", {}).items():
                if value is not None and type(value) is not bool:
                    errors.append(f"invalid prediction {name}")
            for assessment in [*row.get("assessments", []), *row.get("robustness", [])]:
                try:
                    context = MonitorContext.model_validate(assessment["observation"])
                    refs = assessment.get("evidence_event_ids", assessment.get("assessment", {}).get("evidence_event_ids", []))
                    validate_evidence(context, refs)
                    if context.workflow_id != row["workflow_id"]:
                        errors.append("foreign workflow observation")
                    if "ground_truth" in assessment["observation"]:
                        errors.append("ground truth in inference context")
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    errors.append(f"invalid observation/evidence: {type(exc).__name__}")
    if len(rows) > planned:
        errors.append("too many trials")
    try:
        if record["metrics"] != analyze(rows, planned):
            errors.append("metrics do not reproduce")
    except (ValueError, KeyError, TypeError):
        errors.append("malformed metric inputs")
    if record.get("results_hash") and record["results_hash"] != digest(rows):
        errors.append("result artifact hash mismatch")
    return {"valid": not errors, "errors": errors, "planned": planned, "recorded": len(rows),
            "unrun": max(0, planned-len(rows)), "failed": sum(not r.get("evaluation_valid") for r in rows),
            "limitations": "Schema/evidence integrity is not semantic correctness or proof of no label leakage."}
