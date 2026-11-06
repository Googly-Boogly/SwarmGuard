"""Ethical-assessment, council, and workflow-context schemas.

These schemas are the contract between SwarmGuard and its ethics monitors. In
Stage 1 only clearly labeled mock monitors produce them; Stage 2 will add
LLM-backed monitors that must return the same structures.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from swarmguard.models.actions import ActionRequest
from swarmguard.models.approvals import ApprovalRecord
from swarmguard.models.events import WorkflowEvent
from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.workflows import ResourceVersion, SharedResource

if TYPE_CHECKING:  # pragma: no cover
    from swarmguard.models.risk import MonitorContext, WorkflowRiskAssessment

__all__ = ["CouncilReport", "EthicalAssessment", "EthicalFramework", "FrameworkPosition", "MonitorFailure",
           "Recommendation", "RiskLevel", "SPECIALIZED_FRAMEWORKS", "Uncertainty", "WorkflowContext"]


class EthicalFramework(StrEnum):
    VIRTUE_ETHICS = "virtue_ethics"
    CONSEQUENTIALISM = "consequentialism"
    DEONTOLOGY = "deontology"
    CARE_ETHICS = "care_ethics"
    GENERAL_PURPOSE = "general_purpose"  # Stage 3 baseline (not an ethical framework)


SPECIALIZED_FRAMEWORKS: tuple[EthicalFramework, ...] = (
    EthicalFramework.VIRTUE_ETHICS, EthicalFramework.CONSEQUENTIALISM,
    EthicalFramework.DEONTOLOGY, EthicalFramework.CARE_ETHICS,
)


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
    # Stage 3 provenance (optional, additive)
    workflow_id: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=32)
    prompt_hash: str | None = Field(default=None, max_length=64)
    legitimate_interpretations: list[str] = Field(default_factory=list, max_length=10)
    insufficient_evidence: bool = False
    call: dict[str, Any] | None = Field(default=None, description="ProviderResult metadata (usage, latency, ids).")


class MonitorFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    framework: EthicalFramework
    monitor_id: str
    error_type: str
    message: str
    # Stage 3 (optional): structured failure detail
    error_kind: str | None = None
    invalid_evidence_ids: list[str] = Field(default_factory=list)
    call: dict[str, Any] | None = None
    elapsed_ms: float | None = None


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
    # Stage 3 (optional, additive)
    council_type: str = "SPECIALIZED_ETHICS"
    workflow_id: str | None = None
    aggregation_method: str = "UNANIMITY"
    failed_monitors: list[str] = Field(default_factory=list)
    expected_monitors: list[str] = Field(default_factory=list)
    disagreement: dict[str, Any] | None = None
    monitor_elapsed_ms: dict[str, float] = Field(default_factory=dict)


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
    # Stage 2 (optional, additive): structured workflow analysis for future LLM ethics monitors.
    # Populated by ``WorkflowIntelligenceEngine.enrich_ethics_context``; None on Stage 1 paths.
    workflow_analysis: WorkflowRiskAssessment | None = None
    # Stage 3 (optional, additive): the Stage 2 as-of observation (GLOBAL window). When present, LLM
    # monitors serialize THIS as their evidence; it contains no events after the checkpoint.
    observation: MonitorContext | None = None


class CouncilEvaluationRecord(BaseModel):
    """A persisted advisory council result (Stage 3). Never an authorization decision."""

    model_config = ConfigDict(frozen=True)

    evaluation_id: str
    workflow_id: str
    action_id: str
    council_type: str
    experiment_id: str | None = None
    snapshot_digest: str | None = None
    report: CouncilReport
    created_at: datetime
