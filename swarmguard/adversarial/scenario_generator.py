"""Allowlisted parameter generation, independent of labels and monitor implementations."""
from __future__ import annotations
from typing import Any
from swarmguard.llm.base import LLMProvider

from swarmguard.models.adversarial import ResearchConfig, ScenarioDefinition

# Family-disjoint within Stage 5. These inherited fixtures are already public; this
# is operational separation, not a claim of an independently authored blind test.
CATALOG: dict[str, tuple[str, str]] = {
    "approval_mismatch_001": ("development", "approval_integrity"),
    "distributed_approval_001": ("adaptive_search", "distributed_coordination"),
    "distributed_approval_002": ("adaptive_search", "distributed_coordination"),
    "shared_memory_001": ("adaptive_search", "shared_memory"),
    "task_decomposition_001": ("adaptive_search", "task_decomposition"),
    "purpose_violation_001": ("development", "information_flow"),
    "transformed_disclosure_001": ("held_out", "information_flow"),
    "expired_approval_001": ("held_out", "approval_integrity"),
    "legitimate_collaboration_001": ("adaptive_search", "legitimate_collaboration"),
    "legitimate_collaboration_002": ("development", "legitimate_collaboration"),
    "legitimate_shared_update_001": ("development", "shared_memory"),
    "legitimate_decomposition_001": ("held_out", "task_decomposition"),
    "rejected_then_approved_001": ("development", "approval_integrity"),
    "stale_authorization_001": ("held_out", "legitimate_collaboration"),
    "monitor_manipulation_001": ("adaptive_search", "monitor_manipulation"),
}


class ScenarioGenerator:
    async def propose(self, config: ResearchConfig, provider: LLMProvider, parent: ScenarioDefinition,
                      feedback: list[dict[str, Any]]) -> ScenarioDefinition:
        import json
        from swarmguard.ethics.prompts import SCENARIO_GENERATOR_PROMPT
        if config.execution_mode != "openai" or not config.live_authorized:
            raise ValueError("LLM generation requires explicitly authorized live mode")
        allowed = [c.template_id for c in self.generate(config)]
        result = await provider.generate_structured(system_prompt=SCENARIO_GENERATOR_PROMPT,
            user_content=json.dumps({"allowed_templates": allowed, "parent": parent.model_dump(),
                                     "local_search_feedback": feedback}), response_model=ScenarioDefinition,
            request_id=f"generation_{parent.scenario_id}", max_output_tokens=800, seed=config.seed,
            tags={"purpose": "synthetic_scenario_generation"})
        if not result.success or result.response is None:
            raise ValueError("scenario generation failed; no fallback scenario was fabricated")
        return ScenarioDefinition.model_validate(result.response)

    def generate(self, config: ResearchConfig) -> list[ScenarioDefinition]:
        return [ScenarioDefinition(scenario_id=f"candidate_{i:03d}", template_id=tid,
                                   partition=config.partition, agent_count=config.agent_count, max_steps=config.max_steps)
                for i, (tid, (partition, category)) in enumerate(CATALOG.items(), 1)
                if partition == config.partition and config.category in ("all", category)]

