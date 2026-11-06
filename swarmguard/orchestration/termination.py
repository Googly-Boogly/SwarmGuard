"""Execution limits and termination reasons. Limits are trusted configuration; agents cannot change them."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum


class TerminationReason(StrEnum):
    OBJECTIVE_DECLARED_COMPLETE = "OBJECTIVE_DECLARED_COMPLETE"  # a claim by the coordinator, not proof of success
    NO_ELIGIBLE_AGENTS = "NO_ELIGIBLE_AGENTS"
    MAX_WORKFLOW_STEPS = "MAX_WORKFLOW_STEPS"
    MAX_TOOL_REQUESTS = "MAX_TOOL_REQUESTS"
    MAX_MESSAGES = "MAX_MESSAGES"
    MAX_DELEGATIONS = "MAX_DELEGATIONS"
    MAX_RUNTIME = "MAX_RUNTIME"
    LLM_BUDGET_EXHAUSTED = "LLM_BUDGET_EXHAUSTED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"


@dataclass(frozen=True)
class ExecutionLimits:
    max_agent_steps: int = 20
    max_workflow_steps: int = 60
    max_tool_requests: int = 40
    max_messages: int = 30
    max_delegations: int = 12
    max_runtime_seconds: float = 300.0
    max_consecutive_failures: int = 3
    max_idle_rounds: int = 2

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value is None or value <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: int | float) -> ExecutionLimits:
        env = os.environ if env is None else env

        def num(key: str, default: float, cast=int):  # noqa: ANN001,ANN202
            raw = env.get(key, "").strip()
            return cast(raw) if raw else default

        values = {"max_agent_steps": num("SWARMGUARD_MAX_AGENT_STEPS", cls.max_agent_steps),
                  "max_workflow_steps": num("SWARMGUARD_MAX_WORKFLOW_STEPS", cls.max_workflow_steps),
                  "max_runtime_seconds": num("SWARMGUARD_MAX_RUNTIME_SECONDS", cls.max_runtime_seconds, float)}
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def as_dict(self) -> dict[str, float]:
        return asdict(self)
