"""Comparison contracts for existing model, framework and council evaluators."""
from __future__ import annotations
from typing import Any
from swarmguard.research.statistical_analysis import paired_cluster_effect
from swarmguard.research.validation import validate_result

ABLATIONS = {
    "FULL_SYSTEM": ["LOCAL_MONITOR", "GLOBAL_MONITOR", "SPECIALIZED_ETHICS_COUNCIL"],
    "WITHOUT_ETHICS_COUNCIL": ["LOCAL_MONITOR", "GLOBAL_MONITOR"],
    "WITHOUT_GLOBAL_MONITOR": ["LOCAL_MONITOR", "SPECIALIZED_ETHICS_COUNCIL"],
    "WITHOUT_LOCAL_MONITOR": ["GLOBAL_MONITOR", "SPECIALIZED_ETHICS_COUNCIL"],
    "WITHOUT_LLM_MONITORING": ["LOCAL_MONITOR", "GLOBAL_MONITOR"],
    "POLICY_ONLY": [],
}


def ablations(record: dict[str, Any]) -> dict[str, Any]:
    from swarmguard.research.statistical_analysis import analyze
    if not validate_result(record)["valid"]:
        raise ValueError("invalid source experiment")
    rows = []
    for row in record["rows"]:
        predictions = {}
        for name, components in ABLATIONS.items():
            values = [row.get("predictions", {}).get(c) for c in components]
            predictions[name] = True if any(v is True for v in values) else (None if any(v is None for v in values) else False)
        rows.append({**row, "predictions": predictions, "assessments": []})
    return {"experiment_id": record["experiment_id"], "source_hash": record["results_hash"],
            "method": "Recorded prediction OR ablation; no tools or model requests. Not a causal intervention experiment.",
            "components": ABLATIONS, "metrics": analyze(rows, record["metrics"]["accounting"]["planned"]),
            "security": "Gateway authorization is retained in every source trajectory; no policy-removal option exists."}


def compare_experiments(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    differences = [k for k in left["config"] if left["config"].get(k) != right["config"].get(k)]
    if not validate_result(left)["valid"] or not validate_result(right)["valid"]:
        raise ValueError("cannot compare invalid experiment artifacts")
    controls = ("dataset_hash", "authorization_policy_version", "ruleset_version", "prompts", "source_tree_hash")
    differences += [f"manifest.{k}" for k in controls if left["manifest"].get(k) != right["manifest"].get(k)]
    right_rows = {r["trial_id"]: r for r in right["rows"]}
    paired = []
    for row in left["rows"]:
        other = right_rows.get(row["trial_id"])
        if other and row.get("ground_truth") == other.get("ground_truth"):
            paired.append({**row, "evaluation_valid": row.get("evaluation_valid") and other.get("evaluation_valid"),
                "predictions": {"left": row.get("predictions", {}).get(left["config"]["monitor"]),
                                "right": other.get("predictions", {}).get(right["config"]["monitor"])}})
    return {"left": left["experiment_id"], "right": right["experiment_id"], "differences": differences,
            "paired": paired_cluster_effect(paired, "left", "right"),
            "unpaired_rows": len(left["rows"]) + len(right["rows"]) - 2*len(paired),
            "interpretation": "Descriptive comparison only. Material configuration differences confound attribution."}


def ethics_comparison(record: dict[str, Any]) -> dict[str, Any]:
    return {"primary": paired_cluster_effect(record["rows"], "GENERAL_COUNCIL", "SPECIALIZED_ETHICS_COUNCIL"),
            "controls": "Same evidence, four monitors per arm, model, 800-token outputs and aggregation.",
            "note": "Synthetic responders share deterministic rules; equality does not test ethical specialization.",
            "disagreement_and_cost": record["metrics"].get("operational", {})}
