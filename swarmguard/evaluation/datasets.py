"""Synthetic Stage 2 evaluation dataset built from the scenario library.

The dataset is a list of ``Scenario`` objects. Ordering is shuffled by a fixed seed, and
each scenario is given a neutral workflow ID (``wf_001`` …) based on its *shuffled*
position, so workflow IDs carry no information about the scenario or its label.

``composition()`` summarizes labels for reporting. It reads ground truth and must never
be passed to a monitor.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from swarmguard.evaluation.scenarios import stage2_scenarios
from swarmguard.evaluation.scenarios.base import Scenario


@dataclass(frozen=True)
class DatasetItem:
    workflow_id: str
    scenario: Scenario


def build_dataset(seed: int = 0, scenario_ids: Sequence[str] | None = None) -> list[DatasetItem]:
    scenarios = stage2_scenarios()
    known = {s.scenario_id for s in scenarios}
    if scenario_ids is not None:
        unknown = set(scenario_ids) - known
        if unknown:
            raise KeyError(f"unknown scenario ids: {sorted(unknown)}")
        scenarios = [s for s in scenarios if s.scenario_id in set(scenario_ids)]
    order = list(scenarios)
    random.Random(seed).shuffle(order)
    return [DatasetItem(f"wf_{i:03d}", s) for i, s in enumerate(order, 1)]


def composition(items: Sequence[DatasetItem]) -> dict[str, dict[str, int]]:
    truths = [i.scenario.ground_truth() for i in items]
    return {
        "label": dict(sorted(Counter(t.label.value for t in truths).items())),
        "scenario_type": dict(sorted(Counter(t.scenario_type.value for t in truths).items())),
        "safety_property": dict(sorted(Counter(t.safety_property.value for t in truths).items())),
    }
