"""Ethical-assessment, council, and workflow-context schemas.

These schemas are the contract between SwarmGuard and its ethics monitors. In
Stage 1 only clearly labeled mock monitors produce them; Stage 2 will add
LLM-backed monitors that must return the same structures.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from swarmguard.models.actions import ActionRequest
from swarmguard.models.approvals import ApprovalRecord
from swarmguard.models.events import WorkflowEvent
from swarmguard.models.workflows import ResourceVersion, SharedResource


class EthicalFramework(StrEnum):
    VIRTUE_ETHICS = "virtue_ethics"
    CONSEQUENTIALISM = "consequentialism"
    DEONTOLOGY = "deontology"
    CARE_ETHICS = "care_ethics"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Uncertainty(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Recommendation(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"


class EthicalAssessment(BaseModel):
    """One monitor's structured, advisory judgment about one proposed action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    framework: EthicalFramework
    action_id: str = Field(min_length=1, max_length=64)
    risk_level: RiskLevel
    concerns: list[str] = Field(default_factory=list, max_length=20)
    evidence_event_ids: list[str] = Field(
        default_factory=list, max_length=50, description="Event IDs from the workflow context."
    )
    uncertainty: Uncertainty
    recommendation: Recommendation
    explanation: str = Field(min_length=1, max_length=4_000)
    monitor_id: str = Field(default="unspecified", max_length=64)
    is_mock: bool = Field(
        default=False, description="True for Stage 1 deterministic placeholders (not LLM output)."
    )


class MonitorFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    framework: EthicalFramework
    monitor_id: str
    error_type: str
    message: str


class FrameworkPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    framework: EthicalFramework
    recommendation: Recommendation
    risk_level: RiskLevel


class CouncilReport(BaseModel):
    """Aggregated, *advisory* council output. Never authorizes, approves, or executes."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    advisory: Literal[True] = True
    assessments: list[EthicalAssessment]
    failures: list[MonitorFailure] = Field(default_factory=list)
    missing_frameworks: list[EthicalFramework] = Field(default_factory=list)
    positions: list[FrameworkPosition] = Field(default_factory=list)
    disagreement_detected: bool = False
    council_recommendation: Recommendation
    aggregation_rule: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    summary: str
    contains_mock_assessments: bool = False
    created_at: datetime


class WorkflowContext(BaseModel):
    """The identical, structured context handed to every ethics monitor.

    The deterministic policy decision is intentionally *not* included so that
    monitors judge independently (important for RQ2/RQ3 comparisons).
    """

    model_config = ConfigDict(frozen=True)

    workflow_id: str
    action: ActionRequest
    history: list[WorkflowEvent]
    related_actions: list[ActionRequest] = Field(default_factory=list)
    resource: SharedResource | None = None
    resource_history: list[WorkflowEvent] = Field(default_factory=list)
    resource_versions: list[ResourceVersion] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    scope: Literal["FULL_WORKFLOW", "ACTION_ONLY"] = "FULL_WORKFLOW"
