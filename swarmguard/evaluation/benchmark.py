"""Standard comparison entry point; authorization remains mandatory in all arms."""
from typing import Any
from swarmguard.adversarial.adaptive_controller import AdaptiveController
from swarmguard.models.adversarial import ResearchConfig
from swarmguard.storage.database import Database


async def run_benchmark(config: ResearchConfig, db: Database, **kwargs: Any) -> dict[str, Any]:
    if config.kind != "benchmark":
        raise ValueError("benchmark configuration required")
    return await AdaptiveController(db, **kwargs).run(config)
