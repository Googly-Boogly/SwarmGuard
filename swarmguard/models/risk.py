"""Stage 2 models: workflow invariants, monitor observations, findings and assessments.

Separation of concerns (see docs/STAGE2_RESEARCH_DESIGN.md):

* ``WorkflowInvariant``: trusted, explicit workflow-level constraints (configuration,
  like the security policy). Shared identically by every monitor configuration.
* ``WorkflowSnapshot``: what the audit log contained at a checkpoint. Nothing after
  ``cutoff_seq`` is ever included.
* ``MonitorContext``: the observation one monitor receives, derived from a snapshot
  through a controlled observation window (LOCAL or GLOBAL). It never contains
  scenario identifiers, labels, expected outcomes or evaluation metadata.
* ``PatternFinding`` / ``SafetyAssessment``: monitor *predictions*. They are advisory
  and are never authorization decisions (see ``models/decisions.py`` for those).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from swarmguard.models.actions import ActionRequest
from swarmguard.models.approvals import ApprovalStatus, canonical_json, sha256_hex
from swarmguard.models.events import WorkflowEvent
from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.workflows import ResourceType

RULESET_VERSION = "stage2-rules-v1"


class PatternId(StrEnum):
    """Stable pattern identifiers (audit format: add values, never rename)."""

    APPROVAL_SCOPE_DRIFT = "APPROVAL_SCOPE_DRIFT"
    CROSS_AGENT_RESOURCE_CHANGE = "CROSS_AGENT_RESOURCE_CHANGE"
    PROTECTED_DATA_FLOW = "PROTECTED_DATA_FLOW"
    CUMULATIVE_DISCLOSURE = "CUMULATIVE_DISCLOSURE"
    WORKFLOW_INVARIANT_VIOLATION = "WORKFLOW_INVARIANT_VIOLATION"
    UNVERIFIED_SENSITIVE_CONTENT = "UNVERIFIED_SENSITIVE_CONTENT"
    SUSPICIOUS_TASK_DECOMPOSITION = "SUSPICIOUS_TASK_DECOMPOSITION"


class EvidenceBasis(StrEnum):
    """EXPLICIT: follows only from recorded references (IDs, versions, statuses).
    HEURISTIC: relies on an interpretation, e.g. matching synthetic values in free text."""

    EXPLICIT = "EXPLICIT"
    HEURISTIC = "HEURISTIC"


class ObservationWindow(StrEnum):
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"


class InvariantKind(StrEnum):
    RECIPIENT_AUTHORIZATION = "RECIPIENT_AUTHORIZATION"        # Invariant A
    DATA_PURPOSE = "DATA_PURPOSE"                              # Invariant B
    CUMULATIVE_DISCLOSURE = "CUMULATIVE_DISCLOSURE"            # Invariant C
    APPROVAL_SCOPE = "APPROVAL_SCOPE"                          # Invariant D
    PROTECTED_INFORMATION_FLOW = "PROTECTED_INFORMATION_FLOW"  # Invariant E


class WorkflowInvariant(BaseModel):
    """An explicit, trusted constraint over the combined behavior of a workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invariant_id: str
    kind: InvariantKind
    description: str
    scope: str = "workflow"
    severity: RiskLevel = RiskLevel.HIGH
    applies_to_tools: list[str] = Field(default_factory=list)
    protected_resource_types: list[ResourceType] = Field(default_factory=lambda: [ResourceType.CUSTOMER_RECORD])
    protected_fields: list[str] = Field(default_factory=list)
    quasi_identifier_fields: list[str] = Field(default_factory=list)
    max_quasi_identifiers: int = 1
    authorized_recipient_fields: list[str] = Field(default_factory=list)
    consent_field: str | None = None
    sensitive_patterns: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- observations


class ResourceSnapshot(BaseModel):
    """One immutable resource version that existed at the checkpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_id: str
    resource_type: ResourceType
    version: int
    content: dict[str, Any]
    agent_id: str
    action_id: str | None = None
    event_id: str | None = None
    event_seq: int | None = None


class ApprovalView(BaseModel):
    """An approval as it stood at the checkpoint (status reconstructed from events <= cutoff)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    operation: str
    resource_id: str
    resource_version: int
    approved_parameters: dict[str, Any]
    requested_by: str
    requesting_action_id: str | None = None
    status: ApprovalStatus
    approver_id: str | None = None
    expires_at: datetime | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)


class WorkflowSnapshot(BaseModel):
    """The observable state of one workflow as of ``cutoff_seq`` (inclusive)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    cutoff_seq: int
    cutoff_time: datetime | None
    participants: list[str]
    workflow_purpose: str | None
    events: list[WorkflowEvent]
    actions: list[ActionRequest]
    resources: list[ResourceSnapshot]
    approvals: list[ApprovalView]
    agent_roles: dict[str, str] = Field(default_factory=dict)

    def digest(self) -> str:
        return sha256_hex(canonical_json(self.model_dump(mode="json")))


class MonitorContext(BaseModel):
    """The complete input of one safety monitor. Built only by ``ObservationBuilder``.

    Contains no scenario IDs, labels, expected outcomes or evaluation metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: ObservationWindow
    workflow_id: str
    checkpoint_seq: int
    checkpoint_time: datetime | None = None
    action: ActionRequest
    agent_role: str | None = None
    events: list[WorkflowEvent] = Field(default_factory=list)
    prior_actions: list[ActionRequest] = Field(default_factory=list)
    resources: list[ResourceSnapshot] = Field(default_factory=list)
    approvals: list[ApprovalView] = Field(default_factory=list)
    workflow_purpose: str | None = None
    invariants: list[WorkflowInvariant] = Field(default_factory=list)
    outside_window: list[str] = Field(
        default_factory=list, description="Categories of information deliberately excluded by the window."
    )
    snapshot_digest: str = ""

    def event_ids(self) -> set[str]:
        return {e.event_id for e in self.events} | {r.event_id for r in self.resources if r.event_id} | {
            i for a in self.approvals for i in a.evidence_event_ids}


# --------------------------------------------------------------------------- findings and assessments


class FlowHop(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_id: str
    version: int | None = None
    event_id: str | None = None
    agent_id: str | None = None
    relation: str = Field(description="e.g. SOURCE_READ, CONTAINS_VALUE, OUTBOUND_ACTION, PRIOR_DISCLOSURE")
    basis: EvidenceBasis


class DataFlowTrace(BaseModel):
    """Synthetic protected values of one source record found in one outbound operation."""

    model_config = ConfigDict(frozen=True)

    source_resource_id: str
    sink_action_id: str
    recipient: str | None
    protected_fields: list[str] = Field(default_factory=list)
    quasi_identifier_fields: list[str] = Field(default_factory=list)
    recipient_authorized: bool
    hops: list[FlowHop] = Field(default_factory=list)


class PatternFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern_id: PatternId
    severity: RiskLevel
    basis: EvidenceBasis
    is_violation: bool = Field(description="True only for explicit workflow-invariant / approval-scope violations.")
    actionable: bool = Field(description="Whether this finding drives the monitor's recommendation.")
    invariant_id: str | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    affected_resources: list[str] = Field(default_factory=list)
    involved_agents: list[str] = Field(default_factory=list)
    explanation: str
    detected_at_seq: int
    detected_at: datetime | None = None


class SafetyAssessment(BaseModel):
    """One monitor's advisory prediction for one action checkpoint. Not an authorization decision."""

    model_config = ConfigDict(frozen=True)

    monitor_id: str
    window: ObservationWindow
    workflow_id: str
    action_id: str
    checkpoint_seq: int
    risk_level: RiskLevel
    recommendation: Recommendation
    detected_patterns: list[PatternId] = Field(default_factory=list)
    findings: list[PatternFinding] = Field(default_factory=list)
    data_flows: list[DataFlowTrace] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    explanation: str
    uncertainty: Uncertainty
    missing_information: list[str] = Field(default_factory=list)
    method: str = "deterministic_rules"
    ruleset_version: str = RULESET_VERSION
    snapshot_digest: str = ""


class CrossAgentDependency(BaseModel):
    model_config = ConfigDict(frozen=True)

    consumer_action_id: str
    consumer_agent_id: str
    provider_action_id: str
    provider_agent_id: str
    via: str = Field(description="resource_id@vN, or 'parent_action_ids' for explicit action dependencies")
    evidence_event_ids: list[str] = Field(default_factory=list)
    basis: EvidenceBasis = EvidenceBasis.EXPLICIT


class SharedResourceModification(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_id: str
    resource_type: ResourceType
    versions_by_principal: dict[str, list[int]]
    agents: list[str]
    readers: list[str]
    evidence_event_ids: list[str] = Field(default_factory=list)


class ApprovalRelationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    status: ApprovalStatus
    bound_resource_version: str
    requested_by: str
    approver_id: str | None
    citing_action_ids: list[str] = Field(default_factory=list)
    modified_after_binding_by: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)


class WorkflowRiskAssessment(BaseModel):
    """Structured workflow-level analysis (reconstructable from the persisted log)."""

    model_config = ConfigDict(frozen=True)

    workflow_id: str
    analyzed_through_seq: int
    participating_agents: list[str]
    principals: list[str] = Field(description="Agents plus human / system principals that acted.")
    resources_accessed: list[str]
    cross_agent_dependencies: list[CrossAgentDependency] = Field(default_factory=list)
    shared_resource_modifications: list[SharedResourceModification] = Field(default_factory=list)
    approval_relationships: list[ApprovalRelationship] = Field(default_factory=list)
    data_flows: list[DataFlowTrace] = Field(default_factory=list)
    checkpoint_assessments: list[SafetyAssessment] = Field(default_factory=list)
    findings: list[PatternFinding] = Field(default_factory=list)
    risk_level: RiskLevel
    recommendation: Recommendation
    uncertainty: Uncertainty
    explanation: str
    ruleset_version: str = RULESET_VERSION


def _rebuild_forward_refs() -> None:
    from swarmguard.models.assessments import WorkflowContext

    WorkflowContext.model_rebuild(_types_namespace={"WorkflowRiskAssessment": WorkflowRiskAssessment,
                                                    "MonitorContext": MonitorContext})


_rebuild_forward_refs()
