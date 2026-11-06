"""Configuration from environment variables (no API keys needed in Stage 1)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = "swarmguard_demo.db"
    log_level: str = "WARNING"
    approval_ttl_minutes: int = 15
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    results_dir: str = "results"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=os.environ.get("SWARMGUARD_DB_PATH", cls.db_path),
            log_level=os.environ.get("SWARMGUARD_LOG_LEVEL", cls.log_level).upper(),
            approval_ttl_minutes=int(os.environ.get("SWARMGUARD_APPROVAL_TTL_MINUTES", cls.approval_ttl_minutes)),
            api_host=os.environ.get("SWARMGUARD_API_HOST", cls.api_host),
            api_port=int(os.environ.get("SWARMGUARD_API_PORT", cls.api_port)),
            results_dir=os.environ.get("SWARMGUARD_RESULTS_DIR", cls.results_dir),
        )


def configure_logging(level: str = "WARNING") -> None:
    """Standard-library logging, one format for every SwarmGuard component."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
