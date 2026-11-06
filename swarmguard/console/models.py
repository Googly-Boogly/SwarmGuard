"""Strict browser command schemas and persisted control records."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Command):
    password: str = Field(min_length=1, max_length=256)
    username: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class RunConfig(Command):
    scenario_ids: list[str] = Field(min_length=1, max_length=14)
    execution_mode: Literal["MOCK_AUTONOMOUS", "SCRIPTED", "LIVE_AUTONOMOUS"] = "MOCK_AUTONOMOUS"
    monitoring: Literal["NONE", "LOCAL", "GLOBAL", "ETHICS", "GENERAL", "COMBINED"] = "GLOBAL"
    intervention: Literal["OBSERVATION", "WARNING", "HUMAN_OVERSIGHT", "ENFORCEMENT"] = "OBSERVATION"
    aggregation: Literal["MAJORITY", "CONSERVATIVE", "MAJORITY_WITH_ESCALATION"] = "MAJORITY_WITH_ESCALATION"
    trials: int = Field(default=1, ge=1, le=10)
    seed: int = Field(default=0, ge=0, le=2**31-1)
    max_steps: int = Field(default=80, ge=1, le=200)
    max_runtime_seconds: int = Field(default=300, ge=1, le=900)
    max_llm_requests: int = Field(default=400, ge=1, le=2000)
    max_cost_usd: float = Field(default=0.0, ge=0, le=10, allow_inf_nan=False)
    max_concurrency: Literal[1] = 1
    live_confirmed: bool = False

    @model_validator(mode="after")
    def constraints(self) -> RunConfig:
        if len(set(self.scenario_ids)) != len(self.scenario_ids):
            raise ValueError("scenario selection must be unique")
        if len(self.scenario_ids) * self.trials > 50:
            raise ValueError("at most 50 trials per job")
        if self.execution_mode == "LIVE_AUTONOMOUS":
            if not self.live_confirmed:
                raise ValueError("live runs require explicit confirmation")
            raise ValueError("browser live execution is unavailable; use the pre-registered CLI with provider budgets")
        if self.execution_mode == "SCRIPTED":
            # Existing scripts contain a trusted simulated human. They are review-only replays,
            # never the interactive operator path. Only the bounded Stage 1 cases are web-enabled.
            if not set(self.scenario_ids) <= {"authorized_email", "approval_laundering"}:
                raise ValueError("scripted web replay supports only the two Stage 1 scenarios")
            if self.max_steps < 40 or self.intervention != "OBSERVATION" or self.monitoring not in ("NONE", "GLOBAL"):
                raise ValueError("scripted replay requires >=40 steps, observation mode, NONE or GLOBAL monitoring")
        elif set(self.scenario_ids) - {"legitimate_collaboration", "approval_laundering", "distributed_approval"}:
            raise ValueError("unsupported autonomous scenario")
        return self


class JobRecord(BaseModel):
    job_id: str
    kind: Literal["workflow", "experiment"]
    status: str
    config: RunConfig
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    workflow_ids: list[str] = Field(default_factory=list)
    completed_trials: int = 0
    failed_trials: int = 0
    total_trials: int
    current_scenario: str | None = None
    step: int = 0
    active_agent: str | None = None
    error: str | None = None
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_research_result: bool = False


class ApprovalDecision(Command):
    operation_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(default="Operator reviewed exact operation", min_length=1, max_length=500)


class SessionView(BaseModel):
    authenticated: bool
    operator_id: str | None = None
    csrf_token: str | None = None
    login_available: bool
    roles: list[str] = Field(default_factory=list)


class AgentView(BaseModel):
    agent_id: str
    role: str
    display_name: str
    permissions: list[str]
    status: str
    workflow_ids: list[str]
    actions: int
    last_activity: str | None
    task: str | None


class ScenarioView(BaseModel):
    scenario_id: str
    name: str
    description: str
    category: str
    execution_modes: list[str]
    agents: int = 3
    required_capabilities: list[str]
    safety_properties: list[str]
    max_steps: int = 80
