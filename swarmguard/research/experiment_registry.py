"""CAS state transitions and immutable terminal results in the existing platform store."""
from __future__ import annotations
from typing import Any
from swarmguard.clock import Clock
from swarmguard.storage.repositories import PlatformRepository

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INCONCLUSIVE"}
TRANSITIONS = {"CREATED": {"VALIDATED", "FAILED"}, "VALIDATED": {"QUEUED", "FAILED"},
               "QUEUED": {"RUNNING", "CANCELLED", "INCONCLUSIVE"},
               "RUNNING": {"RUNNING", *TERMINAL}}


class ExperimentRegistry:
    def __init__(self, repo: PlatformRepository, clock: Clock) -> None:
        self.repo, self.clock = repo, clock

    def get(self, experiment_id: str) -> tuple[dict[str, Any], int]:
        stored = self.repo.get("experiment", experiment_id)
        if stored is None:
            raise KeyError(experiment_id)
        return stored

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repo.list("experiment", limit)

    def save(self, record: dict[str, Any], version: int, actor: str) -> int:
        previous, current = self.get(record["experiment_id"])
        if current != version:
            raise ValueError("experiment version conflict")
        if record["status"] not in TRANSITIONS.get(previous["status"], set()):
            raise ValueError("invalid transition or immutable terminal experiment")
        for key in ("config", "dataset", "manifest", "created_by"):
            if record[key] != previous[key]:
                raise ValueError("registered experiment configuration is immutable")
        return self.repo.save("experiment", record["experiment_id"], record, expected_version=version,
                              actor=actor, occurred_at=self.clock.now().isoformat())
