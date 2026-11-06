"""Decision enums, stable reason codes, and decision/response models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Decision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"


class ReasonCode(StrEnum):
    EXECUTION_LIMIT_EXCEEDED = "EXECUTION_LIMIT_EXCEEDED"
    """Stable reason codes: part of the audit format. Add codes; never rename existing ones."""

    AUTHORIZED = "AUTHORIZED"
    # Request / identity
    INVALID_ACTION = "INVALID_ACTION"
    INVALID_AGENT_IDENTITY = "INVALID_AGENT_IDENTITY"
    REPLAYED_REQUEST = "REPLAYED_REQUEST"
    # Tools and permissions
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    AGENT_NOT_AUTHORIZED = "AGENT_NOT_AUTHORIZED"
    # Workflow
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    WORKFLOW_NOT_ACTIVE = "WORKFLOW_NOT_ACTIVE"
    WORKFLOW_MISMATCH = "WORKFLOW_MISMATCH"
    NOT_WORKFLOW_PARTICIPANT = "NOT_WORKFLOW_PARTICIPANT"
    # Resources
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_ACCESS_DENIED = "RESOURCE_ACCESS_DENIED"
    RESOURCE_VERSION_MISMATCH = "RESOURCE_VERSION_MISMATCH"
    REQUEST_CONTENT_MISMATCH = "REQUEST_CONTENT_MISMATCH"
    RECIPIENT_NOT_ALLOWED = "RECIPIENT_NOT_ALLOWED"
    INVALID_ASSIGNEE = "INVALID_ASSIGNEE"
    TASK_NOT_ASSIGNED = "TASK_NOT_ASSIGNED"
    # Approvals
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_ALREADY_USED = "APPROVAL_ALREADY_USED"
    APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    APPROVAL_INTEGRITY_FAILURE = "APPROVAL_INTEGRITY_FAILURE"
    UNTRUSTED_APPROVER = "UNTRUSTED_APPROVER"
    # Execution
    EXECUTION_PRECONDITION_FAILED = "EXECUTION_PRECONDITION_FAILED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Advisory
    ETHICS_ESCALATION = "ETHICS_ESCALATION"
    # Stage 2: trusted workflow-invariant guard (enforcement mode only; can only escalate)
    WORKFLOW_RISK_ESCALATION = "WORKFLOW_RISK_ESCALATION"
    # Stage 4
    INVALID_RECIPIENT_AGENT = "INVALID_RECIPIENT_AGENT"
    DELEGATION_EXCEEDS_PERMISSIONS = "DELEGATION_EXCEEDS_PERMISSIONS"
    RECIPIENT_OUT_OF_WORKFLOW_SCOPE = "RECIPIENT_OUT_OF_WORKFLOW_SCOPE"


class PolicyDecision(BaseModel):
    """Output of the deterministic policy engine."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    decision: Decision
    reason_code: ReasonCode
    explanation: str
    checks_passed: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    policy_version: str = ""


class DecisionRecord(BaseModel):
    """The final, persisted gateway decision for one action."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    action_id: str
    workflow_id: str
    agent_id: str
    tool: str
    decision: Decision
    reason_code: ReasonCode
    explanation: str
    details: dict[str, Any] = Field(default_factory=dict)
    policy_decision: Decision
    policy_reason_code: ReasonCode
    checks_passed: list[str] = Field(default_factory=list)
    advisory_recommendation: Decision | None = None
    advisory_summary: str | None = None
    executed: bool = False
    execution_status: str = "NOT_EXECUTED"
    policy_version: str = ""
    decided_at: datetime


class GatewayResponse(BaseModel):
    """Structured response returned to the proposing agent."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    workflow_id: str
    tool: str
    decision: Decision
    reason_code: ReasonCode
    explanation: str
    details: dict[str, Any] = Field(default_factory=dict)
    executed: bool
    execution_result: dict[str, Any] | None = None
