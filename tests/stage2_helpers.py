"""Helpers shared by the Stage 2 tests."""

from __future__ import annotations

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.evaluation.scenarios import stage2_scenarios
from swarmguard.evaluation.scenarios.base import Scenario, ScenarioRun
from swarmguard.models.risk import MonitorContext, ObservationWindow
from tests.conftest import run

SCENARIOS = {s.scenario_id: s for s in stage2_scenarios()}


def scenario(scenario_id: str) -> Scenario:
    return type(SCENARIOS[scenario_id])()


def run_scenario(scenario_id: str, *, path=":memory:", workflow_id: str = "wf_001",
                 enforcement: ObservationWindow | None = None, sim: SwarmSimulator | None = None
                 ) -> tuple[SwarmSimulator, ScenarioRun]:
    sim = sim or SwarmSimulator(path, clock=SteppingClock(), workflow_enforcement=enforcement)
    return sim, run(scenario(scenario_id).run(sim, workflow_id))


def contexts(sim: SwarmSimulator, workflow_id: str, action_id: str) -> tuple[MonitorContext, MonitorContext]:
    builder = sim.observation_builder()
    snap = builder.snapshot_for_action(action_id)
    return builder.local(snap, action_id), builder.global_(snap, action_id)


def action_of_tool(sim: SwarmSimulator, workflow_id: str, tool: str, nth: int = 0) -> str:
    return [a.action_id for a in sim.monitor.get_workflow_actions(workflow_id) if a.tool == tool][nth]
