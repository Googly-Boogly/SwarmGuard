"""Workflow, resource, summary, and dependency-graph models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"


class WorkflowRecord(BaseModel):
    """Trusted workflow metadata (participants are security-relevant)."""

    model_config = ConfigDict(frozen=True)

    workflow_id: str
    title: str
    status: WorkflowStatus
    participants: list[str]
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceType(StrEnum):
    DOCUMENT = "document"
    EMAIL_DRAFT = "email_draft"
    CUSTOMER_RECORD = "customer_record"
    TASK = "task"
    WORKFLOW_METADATA = "workflow_metadata"


class ResourceACL(BaseModel):
    """Agent-level access. ``"*"`` means any workflow participant permitted by role."""

    model_config = ConfigDict(frozen=True)

    read: list[str] = Field(default_factory=lambda: ["*"])
    write: list[str] = Field(default_factory=list)

    def can_read(self, agent_id: str) -> bool:
        return "*" in self.read or agent_id in self.read

    def can_write(self, agent_id: str) -> bool:
        return "*" in self.write or agent_id in self.write


class SharedResource(BaseModel):
    """Boundary A: agent-editable shared state (mutated only via authorized tools)."""

    model_config = ConfigDict(frozen=True)

    resource_id: str
    resource_type: ResourceType
    workflow_id: str | None
    version: int
    content: dict[str, Any]
    acl: ResourceACL
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class ResourceVersion(BaseModel):
    """Immutable snapshot of one resource version (audit, Boundary C)."""

    model_config = ConfigDict(frozen=True)

    resource_id: str
    version: int
    content: dict[str, Any]
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    agent_id: str
    action_id: str | None
    event_id: str | None
    created_at: datetime


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow: WorkflowRecord
    event_count: int
    events_by_type: dict[str, int]
    agents_involved: list[str]
    action_count: int
    decisions_by_outcome: dict[str, int]
    blocked_actions: list[dict[str, str]]
    approvals: list[dict[str, Any]]
    resources: list[dict[str, Any]]
    simulated_emails_sent: int


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------


class NodeType(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    ACTION = "ACTION"
    RESOURCE_VERSION = "RESOURCE_VERSION"
    APPROVAL = "APPROVAL"
    DECISION = "DECISION"
    EXECUTION = "EXECUTION"


class EdgeType(StrEnum):
    PROPOSED_BY = "PROPOSED_BY"          # action -> agent
    ASSIGNED_BY = "ASSIGNED_BY"          # task version -> coordinator agent
    ASSIGNED_TO = "ASSIGNED_TO"          # task version -> assignee agent
    DEPENDS_ON = "DEPENDS_ON"            # action -> parent action (explicit parent_action_ids)
    READS_FROM = "READS_FROM"            # action -> resource version it read
    MODIFIES = "MODIFIES"                # action -> resource version it produced
    SUPERSEDES = "SUPERSEDES"            # resource version -> previous version
    REQUESTS_APPROVAL = "REQUESTS_APPROVAL"  # action -> approval it requested
    BINDS_TO = "BINDS_TO"                # approval -> resource version it covers
    APPROVED_BY = "APPROVED_BY"          # approval -> human
    REJECTED_BY = "REJECTED_BY"          # approval -> human
    INVALIDATED_BY = "INVALIDATED_BY"    # approval -> action whose modification invalidated it
    CITES_APPROVAL = "CITES_APPROVAL"    # action -> approval referenced in the request
    CONSUMED_BY = "CONSUMED_BY"          # approval -> action that consumed it
    DECIDED = "DECIDED"                  # action -> decision
    RESULTED_IN = "RESULTED_IN"          # decision -> execution outcome
    EXECUTED_AFTER = "EXECUTED_AFTER"    # action -> previous action in sequence (chronological, NOT causal)


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    node_type: NodeType
    label: str
    ref_table: str | None = Field(default=None, description="Persisted table backing this node.")
    ref_id: str | None = Field(default=None, description="Primary key in ref_table.")
    event_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    edge_type: EdgeType
    causal: bool = Field(description="True only for edges derived from explicit references.")
    evidence_event_ids: list[str] = Field(default_factory=list)


class WorkflowGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def edges_of(self, edge_type: EdgeType) -> list[GraphEdge]:
        return [e for e in self.edges if e.edge_type is edge_type]


class ActionDependencies(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    direct_parents: list[str]
    ancestors: list[str] = Field(description="Transitive closure over DEPENDS_ON and data provenance.")
    resources_read: list[str] = Field(description="resource_id@vN nodes read by this action or its ancestors.")
    data_providers: list[str] = Field(description="Actions that produced resource versions this action relied on.")
    approvals: list[str]


class ApprovalChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    status: str
    requested_by_action: str | None
    requested_by_agent: str
    request_ancestors: list[str]
    bound_resource_version: str
    approver_id: str | None
    invalidated_by_actions: list[str]
    citing_actions: list[dict[str, str]]
    consumed_by_action: str | None
