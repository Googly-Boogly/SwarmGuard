"""Pre-registered, bounded configurations. Unsupported combinations fail before calls."""
from __future__ import annotations
import random
from typing import Any, Literal
from pydantic import Field, model_validator
from swarmguard.datasets.registry import DatasetDefinition, DatasetRegistry, PARTITIONS
from swarmguard.models.adversarial import StrictModel, ResearchConfig, MonitorName, ScenarioDefinition


class ExperimentConfig(StrictModel):
    question: Literal["RQ1", "RQ2", "RQ3", "RQ4", "RQ5", "RQ6", "RQ7"] = "RQ1"
    dataset: str = Field(default="swarmguard_adaptive_search", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    dataset_version: str = Field(default="1.0", pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    mode: Literal["SCRIPTED", "MOCK_AUTONOMOUS", "LIVE_AUTONOMOUS", "RECORDED_REPLAY"] = "SCRIPTED"
    framework: Literal["native", "custom", "crewai", "langgraph", "autogen"] = "native"
    monitor: MonitorName = "GLOBAL_MONITOR"
    compare_all: bool = True
    intervention: Literal["OBSERVE", "WARN", "ESCALATE", "TRUSTED_ENFORCEMENT"] = "OBSERVE"
    trials: int = Field(default=1, ge=1, le=3)
    max_total_trials: int | None = Field(default=None, ge=1, le=60)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    max_steps: int = Field(default=60, ge=1, le=60)
    max_requests: int = Field(default=500, ge=1, le=1000)
    max_runtime_seconds: int = Field(default=300, ge=1, le=600)
    concurrency: int = Field(default=4, ge=1, le=4)
    max_cost: float = Field(default=0.0, ge=0, le=5, allow_inf_nan=False)
    max_dataset_size: int = Field(default=20, ge=1, le=20)
    aggregation: Literal["MAJORITY_WITH_ESCALATION"] = "MAJORITY_WITH_ESCALATION"
    analysis: Literal["DESCRIPTIVE_PAIRED"] = "DESCRIPTIVE_PAIRED"
    replay_from: str | None = Field(default=None, pattern=r"^experiment_[0-9]+$")

    @model_validator(mode="after")
    def supported(self) -> ExperimentConfig:
        if self.mode == "LIVE_AUTONOMOUS":
            raise ValueError("Unified live autonomous research is not verified; use the separate explicitly authorized provider CLI")
        if self.framework != "native":
            raise ValueError("This dataset uses native templates; framework comparisons use /integrations and run_framework_evaluation.py")
        if self.intervention != "OBSERVE":
            raise ValueError("Primary predictions are observational; policy/local/global enforcement is measured in separate paired replays")
        if (self.mode == "RECORDED_REPLAY") != bool(self.replay_from):
            raise ValueError("recorded replay requires exactly one source experiment")
        if self.max_cost != 0:
            raise ValueError("synthetic and recorded modes have zero billing; use max_cost=0")
        return self

    def runner_config(self, partition: str) -> ResearchConfig:
        return ResearchConfig(kind="benchmark", partition=PARTITIONS[partition], monitor=self.monitor,
            compare_all=self.compare_all, seed=self.seed, trials=self.trials, max_steps=self.max_steps,
            max_requests=self.max_requests, max_concurrency=self.concurrency,
            max_runtime_seconds=self.max_runtime_seconds, max_cost_usd=self.max_cost)

    def trial_schedule(self, definitions: list[ScenarioDefinition]) -> list[tuple[int, ScenarioDefinition]]:
        """Select the bounded schedule before inference, without consulting labels."""
        schedule = [(trial, definition) for trial in range(self.trials) for definition in definitions]
        random.Random(self.seed).shuffle(schedule)
        return schedule if self.max_total_trials is None else schedule[:self.max_total_trials]


def validate_config(config: ExperimentConfig, datasets: DatasetRegistry) -> dict[str, Any]:
    config = ExperimentConfig.model_validate(config.model_dump())
    record = datasets.validate_current(config.dataset, config.dataset_version)
    definition = DatasetDefinition.model_validate(record["definition"])
    if len(definition.scenarios) > config.max_dataset_size or len(definition.scenarios) * config.trials > 60:
        raise ValueError("dataset/trial size exceeds the declared execution limit")
    if config.mode == "MOCK_AUTONOMOUS" and any(s.template_id not in (
        "legitimate_collaboration_001", "distributed_approval_001") or s.agent_count != 3 or
        s.background_rounds or s.shared_writes or s.combine_fragments or s.reverse_order or s.approval_delay_reads
        for s in definition.scenarios):
        raise ValueError("mock autonomous mode supports the registered three-agent autonomous dataset only")
    import os
    for value, env, default in ((config.max_requests, "SWARMGUARD_MAX_LLM_REQUESTS", 1000),
                               (config.max_steps, "SWARMGUARD_MAX_WORKFLOW_STEPS", 60),
                               (config.concurrency, "SWARMGUARD_MAX_CONCURRENT_REQUESTS", 4),
                               (config.max_runtime_seconds, "SWARMGUARD_MAX_RUNTIME_SECONDS", 600)):
        if value > int(os.environ.get(env, str(default))):
            raise ValueError(f"configuration exceeds server limit {env}")
    return record
