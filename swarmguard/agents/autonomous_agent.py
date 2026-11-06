"""LLM-driven autonomous agent.

The agent holds ONLY its read-only identity, an ``LLMProvider`` and its private ``AgentMemory``.
It has no gateway client, workspace, database, executor, approval manager or policy. It proposes;
the trusted ``AgentRuntime`` routes the proposal through the capability gateway with the agent's
credential-bound client.

In live mode every decision is model-generated. Nothing substitutes scripted behavior. In mock
mode the provider's responder is a deterministic hand-written policy (``mock_policies``), clearly
labelled synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from swarmguard.agents.agent_memory import AgentMemory
from swarmguard.agents.prompts import prompt_hash, system_prompt, user_content
from swarmguard.llm.base import LLMProvider
from swarmguard.models.agent_state import ActionResult, AgentDecision, AgentObservation
from swarmguard.security.identity import AgentIdentity


class AgentContext(BaseModel):
    """Permitted, runtime-built inputs for one agent turn (everything except the agent's own memory)."""

    model_config = ConfigDict(frozen=True)

    workflow_id: str
    step: int
    steps_remaining: int
    objective: str | None
    objective_fields: dict[str, Any]
    private_objective: str | None = None
    private_objective_fields: dict[str, Any] = {}
    allowed_tools: list[str]
    workflow_participants: list[str]
    tasks: list[dict[str, Any]]
    inbox: list[dict[str, Any]]
    readable_resources: list[dict[str, Any]]
    my_approval_requests: list[dict[str, Any]]


@dataclass
class DecisionOutcome:
    decision: AgentDecision | None
    raw: dict[str, Any] | None
    provider: dict[str, Any] = field(default_factory=dict)
    error_kind: str | None = None
    error: str | None = None


class AutonomousAgent:
    def __init__(self, identity: AgentIdentity, provider: LLMProvider, memory: AgentMemory, *,
                 adversarial: bool = False, max_output_tokens: int = 600, temperature: float | None = None,
                 seed: int | None = None) -> None:
        self.__identity = identity
        self._provider = provider
        self.memory = memory
        self.adversarial = adversarial
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.seed = seed

    @property
    def identity(self) -> AgentIdentity:
        return self.__identity

    @property
    def agent_id(self) -> str:
        return self.__identity.agent_id

    @property
    def role(self) -> str:
        return self.__identity.role.value

    def configuration(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "role": self.role, "adversarial": self.adversarial,
                "provider": self._provider.name, "model": self._provider.model,
                "is_synthetic": self._provider.is_synthetic, "prompt_hash": prompt_hash(self.role, adversarial=self.adversarial),
                "max_output_tokens": self.max_output_tokens,
                "temperature": self.temperature if self.temperature is not None else "provider default", "seed": self.seed}

    async def observe(self, context: AgentContext) -> AgentObservation:
        return AgentObservation(agent_id=self.agent_id, role=self.role, memory=self.memory.entries(),
                                pinned_references=self.memory.pinned_references, **context.model_dump())

    async def propose_next_action(self, observation: AgentObservation) -> DecisionOutcome:
        result = await self._provider.generate_structured(
            system_prompt=system_prompt(self.role, adversarial=self.adversarial), user_content=user_content(observation),
            response_model=AgentDecision, request_id=f"{self.agent_id}:{observation.workflow_id}:{observation.step}",
            max_output_tokens=self.max_output_tokens, temperature=self.temperature, seed=self.seed,
            tags={"agent_id": self.agent_id, "role": self.role, "workflow_id": observation.workflow_id,
                  "step": str(observation.step)})
        meta = result.model_dump(mode="json", exclude={"response"})
        if not result.success or result.response is None:
            return DecisionOutcome(None, None, meta, result.error.kind.value if result.error else "PROVIDER_ERROR",
                                   result.error.message if result.error else "no response")
        try:
            return DecisionOutcome(AgentDecision.model_validate(result.response), dict(result.response), meta)
        except (ValidationError, ValueError) as exc:
            return DecisionOutcome(None, dict(result.response), meta, "INVALID_DECISION", str(exc)[:300])

    async def receive_result(self, result: ActionResult) -> None:
        payload = result.model_dump(mode="json")
        if payload.get("result") and isinstance(payload["result"].get("content"), dict):
            payload["result"]["content"] = {k: (v[:1500] if isinstance(v, str) else v)
                                            for k, v in payload["result"]["content"].items()}
        self.memory.add("result", payload)
