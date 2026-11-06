"""Controlled adversarial agent (research only, synthetic environment only).

The adversary is an ordinary registered identity with an existing role (no new permissions,
no special tools). It differs only by a *private objective* given to its model, defined by the
scenario configuration. It receives no credentials beyond its gateway-bound client, no network,
no shell, no database handle, no approval or policy access. It is subject to exactly the same
identity, policy and approval checks as every other agent.

Activation requires an explicit ``AdversarialConfig``: ``experiment_mode="ADVERSARIAL"``,
``adversarial_agent_enabled=True`` and ``environment="SYNTHETIC"``. Anything else is rejected.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from swarmguard.models.actions import ID_PATTERN
from swarmguard.security.identity import AgentRole

ADVERSARY_ID = "contractor_agent"


class AdversarialActivationError(ValueError):
    pass


class AdversarialConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_mode: Literal["ADVERSARIAL"]
    adversarial_agent_enabled: bool
    environment: Literal["SYNTHETIC"]
    scenario_id: str
    agent_id: str = Field(default=ADVERSARY_ID, pattern=ID_PATTERN)
    role: AgentRole = AgentRole.RESEARCH
    objective: str = Field(min_length=1, max_length=2_000)
    objective_fields: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enabled(self) -> AdversarialConfig:
        if not self.adversarial_agent_enabled:
            raise AdversarialActivationError("adversarial_agent_enabled must be true to register an adversary")
        if self.agent_id in ("coordinator", "research_agent", "operations_agent", "human_operator"):
            raise AdversarialActivationError("the adversary needs its own identity")
        return self
