"""Data-only imports, exact versions, and an oracle that never enters inference inputs."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import Field
from swarmguard.adversarial.constraints import validate_candidate
from swarmguard.adversarial.scenario_generator import CATALOG
from swarmguard.clock import Clock, SystemClock
from swarmguard.evaluation.scenarios.adaptive_coordination import ParameterizedScenario
from swarmguard.models.adversarial import ResearchConfig, ScenarioDefinition, StrictModel
from swarmguard.research.reproducibility import digest, source_hash
from swarmguard.storage.repositories import PlatformRepository

PARTITIONS = {"DEVELOPMENT": "development", "ADAPTIVE_SEARCH": "adaptive_search",
              "VALIDATION": "development", "HELD_OUT_TEST": "held_out"}


class DatasetDefinition(StrictModel):
    dataset_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,32}$")
    description: str = Field(min_length=1, max_length=1000)
    partition: Literal["DEVELOPMENT", "ADAPTIVE_SEARCH", "VALIDATION", "HELD_OUT_TEST"]
    scenarios: list[ScenarioDefinition] = Field(min_length=1, max_length=20)
    parent_version: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,32}$")
    change_summary: str = Field(default="Initial registration", min_length=1, max_length=500)
    creation_method: Literal["trusted_templates", "validated_template_import"] = "trusted_templates"


def validate_definition(definition: DatasetDefinition) -> DatasetDefinition:
    definition = DatasetDefinition.model_validate(definition.model_dump())
    ids = [s.scenario_id for s in definition.scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate scenario identifiers")
    config = ResearchConfig(kind="benchmark", partition=PARTITIONS[definition.partition])
    for item in definition.scenarios:
        errors = validate_candidate(item, config)
        if errors:
            raise ValueError("; ".join(errors))
        truth = ParameterizedScenario(item).ground_truth()
        if truth.label.value not in ("SAFE", "UNSAFE") or not truth.participating_agents:
            raise ValueError("missing trusted label or participating identities")
    return definition


class DatasetRegistry:
    def __init__(self, repo: PlatformRepository, clock: Clock | None = None) -> None:
        self.repo, self.clock = repo, clock or SystemClock()

    def register(self, definition: DatasetDefinition, actor: str) -> dict[str, Any]:
        definition = validate_definition(definition)
        key = f"{definition.dataset_id}@{definition.version}"
        if self.repo.get("dataset", key):
            raise ValueError("dataset versions are immutable; select a new version")
        parent = None
        if definition.parent_version:
            parent = self.get(definition.dataset_id, definition.parent_version)
            if parent["definition"]["partition"] != definition.partition:
                raise ValueError("dataset revisions cannot change partition")
        truth_hashes = {s.scenario_id: digest(ParameterizedScenario(s).ground_truth().model_dump(mode="json"))
                        for s in definition.scenarios}
        payload = {"definition": definition.model_dump(), "truth_hashes": truth_hashes,
                   "scenario_source_hash": source_hash("swarmguard/evaluation/scenarios/**/*.py"),
                   "parent_hash": parent["content_hash"] if parent else None}
        record = {**payload, "content_hash": digest(payload), "scenario_count": len(definition.scenarios),
                  "validation_status": "VALID", "created_at": self.clock.now().isoformat(),
                  "categories": sorted({CATALOG[s.template_id][1] for s in definition.scenarios}),
                  "limitations": ["Public same-author synthetic fixtures; not an independent blind benchmark.",
                    "Dependencies, identities, tools and resources are fixed by trusted templates; imports cannot define them.",
                    "VALIDATION uses development templates; it is not an independent partition."]}
        self.repo.create("dataset", key, record, actor=actor, occurred_at=self.clock.now().isoformat())
        return record

    def get(self, dataset_id: str, version: str) -> dict[str, Any]:
        stored = self.repo.get("dataset", f"{dataset_id}@{version}")
        if stored is None:
            raise ValueError("unknown dataset version")
        record = stored[0]
        payload = {k: record[k] for k in ("definition", "truth_hashes", "scenario_source_hash", "parent_hash")}
        if digest(payload) != record["content_hash"]:
            raise ValueError("dataset content hash mismatch")
        return record

    def validate_current(self, dataset_id: str, version: str) -> dict[str, Any]:
        record = self.get(dataset_id, version)
        definition = validate_definition(DatasetDefinition.model_validate(record["definition"]))
        if record["scenario_source_hash"] != source_hash("swarmguard/evaluation/scenarios/**/*.py"):
            raise ValueError("scenario implementation changed; register a new dataset version")
        if record["truth_hashes"] != {s.scenario_id: digest(ParameterizedScenario(s).ground_truth().model_dump(mode="json"))
                                      for s in definition.scenarios}:
            raise ValueError("trusted scoring specification changed")
        return record

    def list(self) -> list[dict[str, Any]]:
        return self.repo.list("dataset")

    def seed(self) -> None:
        for partition in ("DEVELOPMENT", "ADAPTIVE_SEARCH", "HELD_OUT_TEST"):
            dataset_id = f"swarmguard_{partition.lower()}"
            if self.repo.get("dataset", f"{dataset_id}@1.0"):
                continue
            definitions = [ScenarioDefinition(scenario_id=f"case_{i:03}", template_id=tid, partition=p)
                           for i, (tid, (p, _)) in enumerate(CATALOG.items()) if p == PARTITIONS[partition]]
            self.register(DatasetDefinition(dataset_id=dataset_id, version="1.0", partition=partition,
                description=f"Existing synthetic {partition.lower()} fixtures", scenarios=definitions), "swarmguard.datasets")
        if not self.repo.get("dataset", "swarmguard_autonomous@1.0"):
            self.register(DatasetDefinition(dataset_id="swarmguard_autonomous", version="1.0",
                partition="ADAPTIVE_SEARCH", description="Two controlled mock autonomous service objectives",
                scenarios=[ScenarioDefinition(scenario_id=f"autonomous_{i}", template_id=t)
                    for i, t in enumerate(("legitimate_collaboration_001", "distributed_approval_001"))]), "swarmguard.datasets")

    def compare(self, dataset_id: str, left: str, right: str) -> dict[str, Any]:
        a, b = self.get(dataset_id, left), self.get(dataset_id, right)
        x = {s["scenario_id"]: s for s in a["definition"]["scenarios"]}
        y = {s["scenario_id"]: s for s in b["definition"]["scenarios"]}
        return {"left_hash": a["content_hash"], "right_hash": b["content_hash"],
                "added": sorted(y.keys() - x.keys()), "removed": sorted(x.keys() - y.keys()),
                "changed": sorted(k for k in x.keys() & y.keys() if x[k] != y[k])}
