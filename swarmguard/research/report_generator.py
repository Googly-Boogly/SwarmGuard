"""Reports quote recorded measurements only; discussion remains a researcher-review draft."""
from __future__ import annotations
import html
import json
from typing import Any
from swarmguard.research.hypothesis_registry import question_for
from swarmguard.research.validation import validate_result

SECTIONS = ("Title", "Abstract draft", "Research question", "Hypothesis", "Experimental setup", "Dataset description",
            "Monitoring configurations", "Evaluation methodology", "Results", "Statistical analysis", "Failure analysis",
            "Discussion", "Limitations", "Reproducibility information", "Conclusion", "References and artifact information")


def generate_report(record: dict[str, Any], sections: list[str] | None = None, *, concise: bool = False) -> dict[str, Any]:
    validation = validate_result(record)
    if not validation["valid"] or record["status"] not in ("COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"):
        raise ValueError("report requires a terminal experiment with valid recorded artifacts")
    selected = sections or list(SECTIONS)
    if len(set(selected)) != len(selected) or set(selected) - set(SECTIONS):
        raise ValueError("unknown or duplicate report section")
    q = question_for(record)
    if concise:
        return _concise_report(record, validation, q)
    dumps = lambda obj: json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    parts = {
        "Title": f"SwarmGuard research record: {record['experiment_id']}",
        "Abstract draft": f"DRAFT — researcher review required. {record['config']['mode']}; {record['status']}. "
                          f"Recorded {len(record['rows'])} trials. These are synthetic engineering measurements, not live LLM evidence.",
        "Research question": q["title"], "Hypothesis": q["hypothesis"] + " Null: " + q["null_hypothesis"],
        "Experimental setup": dumps(record["config"]), "Dataset description": dumps(record["dataset"]),
        "Monitoring configurations": dumps(sorted(record["metrics"]["detection"])),
        "Evaluation methodology": "Freeze critical-checkpoint predictions before trusted scoring. Separate monitor alerts, gateway decisions, prohibited outcomes and legitimate completion. Paired interventions retain authorization.",
        "Results": dumps(record["metrics"]["detection"]),
        "Statistical analysis": dumps({k: v for k, v in record["metrics"].items() if k != "detection"}),
        "Failure analysis": dumps({"error": record["error"], "accounting": record["metrics"]["accounting"],
                                  "failures": [f for r in record["rows"] for f in r.get("failures", [])]}),
        "Discussion": "DRAFT — researcher review required. Assess false positives and utility alongside prevention. Mock council agreement is constructed, not evidence for philosophical specialization. No causal or significance claim is generated.",
        "Limitations": "Public same-author fixtures, hand-written mock policies, exact-value detectors and critical-only checkpoints. No independent blind study or live model evaluation. Missing responses remain unknown.",
        "Reproducibility information": dumps(record["manifest"]),
        "Conclusion": "This record supports inspection of the executed synthetic experiment. Research hypotheses remain open.",
        "References and artifact information": dumps({"experiment_id": record["experiment_id"],
            "results_sha256": record["results_hash"], "dataset_sha256": record["dataset"]["content_hash"],
            "started_at": record["started_at"], "completed_at": record["completed_at"],
            "repository_documents": ["docs/RESEARCH_METHODOLOGY.md", "docs/SCIENTIFIC_VALIDATION.md"],
            "external_citations": [], "validation": validation}),
    }
    markdown = "\n\n".join(f"## {name}\n\n{parts[name]}" for name in selected) + "\n"
    return {"experiment_id": record["experiment_id"], "results_hash": record["results_hash"],
            "draft": True, "sections": {s: parts[s] for s in selected}, "markdown": markdown,
            "html": '<!doctype html><html lang="en"><meta charset="utf-8"><title>SwarmGuard research report</title>'
                    '<body><pre style="white-space:pre-wrap">' + html.escape(markdown) + '</pre></body></html>',
            "validation": validation}


def _concise_report(record: dict[str, Any], validation: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    """A bounded reading view over fixed measurements; full bundles retain all evidence."""
    metrics = record["metrics"]
    accounting = metrics["accounting"]
    detection = metrics["detection"]
    primary = {
        "RQ1": ("LOCAL_MONITOR", "GLOBAL_MONITOR"),
        "RQ2": ("GENERAL_COUNCIL", "SPECIALIZED_ETHICS_COUNCIL"),
    }.get(record["config"]["question"], tuple(sorted(detection)))

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")[:180]

    def rate(value: float | None) -> str:
        return "Undefined" if value is None else f"{value:.1%}"

    def prediction(row: dict[str, Any], name: str) -> str:
        value = row.get("predictions", {}).get(name)
        return "Unavailable" if not row.get("evaluation_valid") or value is None else "Alert" if value else "No alert"

    lines = [f"# {record['experiment_id']} — {question['research_question_id']}",
        "DRAFT · Synthetic results; descriptive only.", f"**{question['title']}**",
        f"Status: {record['status']} · Mode: {record['config']['mode']} · Seed: {record['config']['seed']}",
        f"Trials: {accounting['recorded']} recorded / {accounting['planned']} planned; "
        f"{accounting['failed']} failed; {accounting['unrun']} unrun. "
        f"Missing monitor predictions: {accounting['missing_monitor_predictions']}.",
        "## Monitor comparison", "| Monitor | n | TP / FN | FP / TN | Recall | False-positive rate | Not evaluated |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name in primary:
        m = detection.get(name)
        if m is None:
            lines.append(f"| {name} | — | — | — | Undefined | Undefined | Unavailable |")
        else:
            lines.append(f"| {name} | {m['n']} | {m['tp']} / {m['fn']} | {m['fp']} / {m['tn']} | "
                         f"{rate(m['recall'])} | {rate(m['false_positive_rate'])} | {m['not_evaluated']} |")
    lines += ["## Trial outcomes", "| Trial | Label | " + " | ".join(primary) + " |",
              "| --- | --- | " + " | ".join("---" for _ in primary) + " |"]
    for row in record["rows"][:10]:
        label = row.get("ground_truth", {}).get("label", "Unscored") if row.get("evaluation_valid") else "Unscored"
        lines.append("| " + " | ".join([cell(row.get("trial_id", "Unavailable")), cell(label),
                                         *(prediction(row, name) for name in primary)]) + " |")
    if len(record["rows"]) > 10:
        lines.append(f"Showing 10 of {len(record['rows'])} trials; all trials are in the reproducibility bundle.")
    if record.get("error"):
        lines.append("Run error: " + cell(record["error"]))
    lines += ["## Interpretation",
        "Recall uses unsafe trials; false-positive rate uses safe trials. Zero denominators are undefined. "
        "Unavailable assessments are unknown. Alerts measure detection, not gateway prevention.",
        "Small, public synthetic samples cannot establish a low population false-positive rate or live-agent effectiveness.",
        "Evidence validation: passed. Full statistics, other monitors, trajectories and provenance remain in the reproducibility bundle."]
    markdown = "\n\n".join(lines[:5]) + "\n\n" + "\n".join(lines[5:]) + "\n"
    return {"experiment_id": record["experiment_id"], "results_hash": record["results_hash"],
            "draft": True, "sections": {"Summary": markdown}, "markdown": markdown,
            "html": '<!doctype html><html lang="en"><meta charset="utf-8"><title>SwarmGuard summary</title>'
                    '<body><pre style="white-space:pre-wrap">' + html.escape(markdown) + '</pre></body></html>',
            "validation": validation}
