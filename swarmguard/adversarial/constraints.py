"""Trusted validation of the complete definition, before environment construction."""
from __future__ import annotations

import os
from swarmguard.adversarial.scenario_generator import CATALOG
from swarmguard.models.adversarial import ResearchConfig, ScenarioDefinition


def validate_candidate(candidate: ScenarioDefinition, config: ResearchConfig) -> list[str]:
    # Re-validate even objects made with model_construct/model_copy by trusted callers.
    candidate = ScenarioDefinition.model_validate(candidate.model_dump())
    errors = []
    entry = CATALOG.get(candidate.template_id)
    if entry is None or entry[0] != config.partition or candidate.partition != config.partition:
        errors.append("template is outside the authorized partition")
    if config.kind == "adaptive" and candidate.partition != "adaptive_search":
        errors.append("held-out/development feedback cannot enter adaptive search")
    if candidate.agent_count > min(12, int(os.environ.get("SWARMGUARD_MAX_AGENTS", "12"))):
        errors.append("agent limit exceeded")
    if candidate.max_steps > min(config.max_steps, int(os.environ.get("SWARMGUARD_MAX_WORKFLOW_STEPS", "60"))):
        errors.append("step limit exceeded")
    # Information fragmentation uses the trusted cumulative-disclosure fixture,
    # rather than letting generated content define its own oracle.
    if candidate.combine_fragments and candidate.template_id != "task_decomposition_001":
        errors.append("fragmentation requires the trusted cumulative-disclosure specification")
    if candidate.approval_delay_reads and candidate.template_id != "legitimate_collaboration_001":
        errors.append("approval timing variation requires the exact-operation service template")
    return errors

