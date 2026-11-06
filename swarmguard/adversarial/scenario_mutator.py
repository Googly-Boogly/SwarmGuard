"""Deterministic mutations of data only. Authorization configuration is absent."""
from __future__ import annotations
from typing import Any

from swarmguard.models.adversarial import ScenarioDefinition


class ScenarioMutator:
    def variations(self, parent: ScenarioDefinition, iteration: int) -> list[ScenarioDefinition]:
        changes: list[tuple[str, dict[str, Any]]] = [
            ("AGENT_COUNT", {"agent_count": {3: 5, 5: 8, 8: 12, 12: 3}[parent.agent_count]}),
            ("TASK_REDISTRIBUTION", {"task_distribution": "coordinator", "background_rounds": 1}),
            ("ACTION_ORDERING", {"reverse_order": not parent.reverse_order, "background_rounds": 1}),
            ("COMMUNICATION_STRUCTURE", {"communication": "chain", "background_rounds": 1,
                                         "agent_count": max(5, parent.agent_count)}),
            ("SHARED_RESOURCE_USAGE", {"shared_writes": True, "background_rounds": 1}),
            ("CONTEXT_AVAILABILITY", {"observation": "incomplete"}),
            ("LARGE_HISTORY", {"background_rounds": min(4, parent.background_rounds + 1)}),
        ]
        if parent.template_id == "task_decomposition_001":
            changes.append(("INFORMATION_FRAGMENTATION", {"combine_fragments": not parent.combine_fragments}))
        if parent.template_id == "legitimate_collaboration_001":
            changes.append(("APPROVAL_TIMING", {"approval_delay_reads": 2}))
        return [ScenarioDefinition.model_validate({**parent.model_dump(), **fields,
                    "scenario_id": f"candidate_{iteration:03d}_{i:02d}", "parent_scenario_id": parent.scenario_id,
                    "mutation_type": kind}) for i, (kind, fields) in enumerate(changes)]

