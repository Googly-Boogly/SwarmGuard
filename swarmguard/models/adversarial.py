"""Stage 5 commands. No policy, identity, approval or executable payload fields."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Partition = Literal["development", "adaptive_search", "held_out"]
MonitorName = Literal["BASELINE_POLICY_ONLY", "LOCAL_MONITOR", "GLOBAL_MONITOR", "SINGLE_GENERAL",
                      "GENERAL_COUNCIL", "SPECIALIZED_ETHICS_COUNCIL", "COMBINED_OVERSIGHT"]
MONITORS = ("BASELINE_POLICY_ONLY", "LOCAL_MONITOR", "GLOBAL_MONITOR", "SINGLE_GENERAL",
            "GENERAL_COUNCIL", "SPECIALIZED_ETHICS_COUNCIL", "COMBINED_OVERSIGHT")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResearchConfig(StrictModel):
    kind: Literal["adaptive", "benchmark", "scaling"] = "adaptive"
    execution_mode: Literal["mock", "openai"] = "mock"
    live_authorized: bool = False
    partition: Partition = "adaptive_search"
    category: Literal["all", "approval_integrity", "distributed_coordination", "information_flow",
                      "shared_memory", "task_decomposition", "monitor_manipulation", "legitimate_collaboration"] = "all"
    research_objective: str = Field(default="Evaluate synthetic cross-agent safety", min_length=1, max_length=500)
    agent_count: Literal[3, 5, 8, 12] = 3
    monitor: MonitorName = "GLOBAL_MONITOR"
    compare_all: bool = True
    max_iterations: int = Field(default=8, ge=1, le=20)
    max_steps: int = Field(default=60, ge=1, le=60)
    trials: int = Field(default=1, ge=1, le=3)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    max_requests: int = Field(default=100, ge=1, le=1000)
    max_concurrency: int = Field(default=4, ge=1, le=4)
    max_runtime_seconds: int = Field(default=600, ge=1, le=600)
    max_cost_usd: float = Field(default=0.0, ge=0, le=5, allow_inf_nan=False)

    @model_validator(mode="after")
    def separated(self) -> ResearchConfig:
        if self.kind == "adaptive" and self.partition != "adaptive_search":
            raise ValueError("adaptive feedback is restricted to adaptive_search")
        if self.kind == "scaling" and (self.partition != "adaptive_search" or self.category not in ("all", "legitimate_collaboration")):
            raise ValueError("scaling uses the registered adaptive_search legitimate collaboration objective")
        if self.execution_mode == "openai" and (not self.live_authorized or self.max_cost_usd <= 0):
            raise ValueError("live research requires explicit authorization and a positive spending limit")
        return self


class ScenarioDefinition(StrictModel):
    scenario_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    template_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    partition: Partition = "adaptive_search"
    environment: Literal["synthetic"] = "synthetic"
    agent_count: Literal[3, 5, 8, 12] = 3
    max_steps: int = Field(default=60, ge=1, le=60)
    background_rounds: int = Field(default=0, ge=0, le=4)
    reverse_order: bool = False
    communication: Literal["star", "chain"] = "star"
    task_distribution: Literal["research", "coordinator"] = "research"
    shared_writes: bool = False
    approval_delay_reads: int = Field(default=0, ge=0, le=3)
    combine_fragments: bool = False
    observation: Literal["original", "incomplete", "reordered", "misleading", "contradictory", "injection"] = "original"
    parent_scenario_id: str | None = None
    mutation_type: str = "INITIAL"


class CandidateFeedback(StrictModel):
    scenario_id: str
    prohibited_action_proposed: bool | None
    prohibited_outcome_occurred: bool | None
    monitor_detected: bool | None
    first_detection_seq: int | None
    gateway_blocked: bool
    gateway_blocks: int
    enforcement_prevented: bool | None
    legitimate_objective_completed: bool | None
    evaluation_valid: bool
    terminated_normally: bool
    observations_incomplete: bool

