"""Structured experiment records and their persistence (JSON + CSV).

Every record is populated from an actual run of ``runner.run_stage2_experiment``.
Nothing in this module fabricates or fills in values. Layout::

    results/<experiment_id>/
        config.json        experiment configuration and metadata (timestamp, versions, revision, seed)
        predictions.json   one row per (scenario, monitor configuration), with ground truth joined after prediction
        checkpoints.json   every monitor assessment at every action checkpoint
        enforcement.json   outcomes of the deterministic-only and enforcement-mode runs
        ground_truth.json  the hand-written scenario labels (kept separate from observations)
        metrics.json       detection and prevention metrics
        summary.csv        flat table of predictions
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from swarmguard.models.actions import ID_PATTERN

RESULT_FILES = ("config.json", "predictions.json", "checkpoints.json", "enforcement.json", "ground_truth.json",
                "metrics.json", "summary.csv")


class CheckpointRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    workflow_id: str
    configuration: str
    step: int = Field(description="1-based index of the action checkpoint within the workflow")
    action_id: str
    tool: str
    agent_id: str
    checkpoint_seq: int
    snapshot_digest: str
    recommendation: str
    risk_level: str
    uncertainty: str
    alert: bool
    detected_patterns: list[str]
    evidence_event_ids: list[str]
    missing_information: list[str]
    explanation: str


class ScenarioPrediction(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    scenario_id: str
    workflow_id: str
    configuration: str
    prediction: str
    detected: bool
    first_detection_step: int | None
    first_detection_action_id: str | None
    critical_step: int
    detected_at_critical_step: bool
    alert_checkpoints: int
    checkpoints: int
    detected_patterns: list[str]
    evidence_event_ids: list[str]
    authorization_result: str
    authorization_reason_code: str
    prohibited_outcome_occurred: bool | None
    # joined from ground truth only AFTER the prediction above was recorded
    ground_truth: str = ""
    safety_property: str = ""
    correct: bool | None = None


class EnforcementOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    configuration: str
    ground_truth: str
    critical_decision: str
    critical_reason_code: str
    escalated_by_workflow_guard: int = Field(description="send_email actions escalated by the guard in this run")
    blocked_by_policy: int = Field(description="send_email actions blocked/escalated by the deterministic policy")
    simulated_emails_sent: int
    prohibited_outcome_occurred: bool | None
    prevented: bool | None = Field(description="Unsafe scenario whose prohibited outcome did not occur; None if safe")


class ExperimentResults(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: dict[str, Any]
    metadata: dict[str, Any]
    predictions: list[ScenarioPrediction]
    checkpoints: list[CheckpointRecord]
    enforcement: list[EnforcementOutcome]
    ground_truth: list[dict[str, Any]]
    metrics: dict[str, Any]
    is_research_result: bool = False
    note: str = ("Controlled benchmark on deterministic synthetic scenarios with deterministic rule-based monitors. "
                 "Infrastructure validation, not evidence about real autonomous agents.")

    @property
    def experiment_id(self) -> str:
        return str(self.config["experiment_id"])


def _dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def save_results(results: ExperimentResults, root: str | Path) -> Path:
    if not re.fullmatch(ID_PATTERN, results.experiment_id):
        raise ValueError("invalid experiment id")
    out = Path(root) / results.experiment_id
    out.mkdir(parents=True, exist_ok=True)
    _dump(out / "config.json", {"config": results.config, "metadata": results.metadata,
                                "is_research_result": results.is_research_result, "note": results.note})
    _dump(out / "predictions.json", [p.model_dump(mode="json") for p in results.predictions])
    _dump(out / "checkpoints.json", [c.model_dump(mode="json") for c in results.checkpoints])
    _dump(out / "enforcement.json", [e.model_dump(mode="json") for e in results.enforcement])
    _dump(out / "ground_truth.json", results.ground_truth)
    _dump(out / "metrics.json", results.metrics)
    fields = list(ScenarioPrediction.model_fields)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for p in results.predictions:
            row = p.model_dump(mode="json")
            writer.writerow({k: (";".join(map(str, v)) if isinstance(v, list) else v) for k, v in row.items()})
    return out


def load_results(root: str | Path, experiment_id: str) -> ExperimentResults:
    if not re.fullmatch(ID_PATTERN, experiment_id):
        raise FileNotFoundError(experiment_id)
    base = Path(root) / experiment_id
    cfg = json.loads((base / "config.json").read_text(encoding="utf-8"))
    load = lambda name: json.loads((base / name).read_text(encoding="utf-8"))  # noqa: E731
    return ExperimentResults(
        config=cfg["config"], metadata=cfg["metadata"], is_research_result=cfg.get("is_research_result", False),
        note=cfg.get("note", ""),
        predictions=[ScenarioPrediction.model_validate(p) for p in load("predictions.json")],
        checkpoints=[CheckpointRecord.model_validate(c) for c in load("checkpoints.json")],
        enforcement=[EnforcementOutcome.model_validate(e) for e in load("enforcement.json")],
        ground_truth=load("ground_truth.json"), metrics=load("metrics.json"),
    )


def list_experiments(root: str | Path) -> list[str]:
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and re.fullmatch(ID_PATTERN, p.name) and (p / "config.json").is_file())


def code_revision(start: str | Path = ".") -> str | None:
    """Git commit of the working tree, read from ``.git`` files (no subprocess). None if unavailable."""
    here = Path(start).resolve()
    for d in (here, *here.parents):
        git = d / ".git"
        if git.is_dir():
            head = (git / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                ref = git / head[5:]
                if ref.is_file():
                    return ref.read_text(encoding="utf-8").strip()
                packed = git / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line.endswith(head[5:]):
                            return line.split(" ", 1)[0]
                return None
            return head or None
    return None
