"""Audit-event models (Boundary C)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    DECISION_RECORDED = "DECISION_RECORDED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_EXECUTION_FAILED = "ACTION_EXECUTION_FAILED"
    RESOURCE_READ = "RESOURCE_READ"
    RESOURCE_CREATED = "RESOURCE_CREATED"
    RESOURCE_MODIFIED = "RESOURCE_MODIFIED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_COMPLETED = "TASK_COMPLETED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    EMAIL_SENT_SIMULATED = "EMAIL_SENT_SIMULATED"
    ETHICS_ASSESSMENT = "ETHICS_ASSESSMENT"
    SECURITY_ALERT = "SECURITY_ALERT"
    WORKFLOW_RISK_ASSESSMENT = "WORKFLOW_RISK_ASSESSMENT"  # Stage 2 enforcement-mode guard output
    # Stage 4 (autonomous runtime)
    MESSAGE_SENT = "MESSAGE_SENT"
    AGENT_DECISION = "AGENT_DECISION"
    AGENT_DECISION_REJECTED = "AGENT_DECISION_REJECTED"
    TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
    INTERVENTION_ISSUED = "INTERVENTION_ISSUED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
    WORKFLOW_TERMINATED = "WORKFLOW_TERMINATED"
    FRAMEWORK_OBSERVATION = "FRAMEWORK_OBSERVATION"
    # Stage 7 security operations (recorded in the reserved ``system_security`` stream)
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    IDENTITY_REGISTERED = "IDENTITY_REGISTERED"
    IDENTITY_REVOKED = "IDENTITY_REVOKED"
    EMERGENCY_STOP_ACTIVATED = "EMERGENCY_STOP_ACTIVATED"
    EMERGENCY_STOP_RECOVERED = "EMERGENCY_STOP_RECOVERED"
    CAPABILITY_STATE_CHANGED = "CAPABILITY_STATE_CHANGED"
    OPERATION_STATE_CHANGED = "OPERATION_STATE_CHANGED"
    OPERATOR_ACTION = "OPERATOR_ACTION"


class ActorType(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    MONITOR = "MONITOR"


class WorkflowEvent(BaseModel):
    """One entry in the local audit log. Append-only by convention; NOT tamper-proof.

    ``parent_event_ids`` are explicit causal references recorded by trusted code
    at write time (never inferred from timestamps). ``related_event_ids`` are
    non-causal references (e.g. evidence cited by a monitor).
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    seq: int
    workflow_id: str
    event_type: EventType
    agent_id: str = Field(description="Responsible principal: agent, human, or swarmguard.* system component.")
    actor_type: ActorType
    action_id: str | None = None
    tool: str | None = None
    resource_id: str | None = None
    resource_version_before: int | None = None
    resource_version_after: int | None = None
    approval_id: str | None = None
    decision: str | None = None
    reason_code: str | None = None
    parent_event_ids: list[str] = Field(default_factory=list)
    related_event_ids: list[str] = Field(default_factory=list)
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
