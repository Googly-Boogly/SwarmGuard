"""Comparable objective, sequential schedules, explicit work-volume confounding."""
from typing import Any
from swarmguard.adversarial.adaptive_controller import AdaptiveController
from swarmguard.models.adversarial import ResearchConfig
from swarmguard.storage.database import Database


async def run_scaling(config: ResearchConfig, db: Database, **kwargs: Any) -> dict[str, Any]:
    if config.kind != "scaling" or config.partition != "adaptive_search":
        raise ValueError("scaling uses the registered adaptive_search service fixture")
    return await AdaptiveController(db, **kwargs).run(config)
