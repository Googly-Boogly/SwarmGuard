"""Reports quote recorded measurements only; discussion remains a researcher-review draft."""
from __future__ import annotations
import html
import json
from typing import Any
from swarmguard.research.hypothesis_registry import questions
from swarmguard.research.validation import validate_result

SECTIONS = ("Title", "Abstract draft", "Research question", "Hypothesis", "Experimental setup", "Dataset description",
            "Monitoring configurations", "Evaluation methodology", "Results", "Statistical analysis", "Failure analysis",
            "Discussion", "Limitations", "Reproducibility information", "Conclusion", "References and artifact information")


def generate_report(record: dict[str, Any], sections: list[str] | None = None) -> dict[str, Any]:
    validation = validate_result(record)
    if not validation["valid"] or record["status"] not in ("COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"):
        raise ValueError("report requires a terminal experiment with valid recorded artifacts")
    selected = sections or list(SECTIONS)
    if len(set(selected)) != len(selected) or set(selected) - set(SECTIONS):
        raise ValueError("unknown or duplicate report section")
    q = next(q for q in questions() if q["research_question_id"] == record["config"]["question"])
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
