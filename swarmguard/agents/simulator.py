"""Trusted simulator: wires components, assigns identities, seeds synthetic data.

The simulator is part of the trusted computing base. It registers each agent
(identity + gateway client), registers the simulated human operator, and then
seals both registries so no new principals can appear at runtime.

All data is synthetic. All addresses use reserved example domains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from swarmguard.agents.coordinator import CoordinatorAgent
from swarmguard.agents.operations_agent import OperationsAgent
from swarmguard.agents.research_agent import ResearchAgent
from swarmguard.agents.workspace import SharedWorkspace
from swarmguard.clock import Clock, SteppingClock
from swarmguard.ethics.council import EthicsCouncil
from swarmguard.models.approvals import ApprovalRecord, ApprovalReviewPacket
from swarmguard.models.workflows import ResourceACL, ResourceType, WorkflowRecord
from swarmguard.security.approval_manager import ApprovalManager, ApproverCredential
from swarmguard.security.capability_gateway import AgentGatewayClient, CapabilityGateway
from swarmguard.security.decision_engine import DecisionEngine
from swarmguard.security.identity import AgentIdentity, AgentRole, IdentityRegistry
from swarmguard.security.policy_engine import DEFAULT_POLICY, PolicyEngine, SecurityPolicy
from swarmguard.security.workflow_graph import WorkflowGraphService
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories

COORDINATOR_ID = "coordinator"
RESEARCH_ID = "research_agent"
OPERATIONS_ID = "operations_agent"
HUMAN_OPERATOR_ID = "human_operator"
OPERATOR_EMAIL = "operator@example.test"
SIMULATOR_ACTOR = "swarmguard.simulator"

SEED_CUSTOMERS: dict[str, dict[str, str]] = {
    "cust_001": {"name": "Alex Example", "email": "customer@example.test",
                 "open_request": "SR-1001: service request received"},
    "cust_002": {"name": "Jordan Sample", "email": "other@example.test", "open_request": "none"},
}


@dataclass
class SimulatedHumanOperator:
    """The trusted (simulated) human. Approves only the snapshot it has reviewed.

    Invoked only by the trusted simulator / test harness. Agents never receive
    this object or its credential.
    """

    operator_id: str
    email: str
    _credential: ApproverCredential
    _approvals: ApprovalManager
    reviewed: list[ApprovalReviewPacket] = field(default_factory=list)

    def review(self, approval_id: str) -> ApprovalReviewPacket:
        packet = self._approvals.review_packet(approval_id)
        self.reviewed.append(packet)
        return packet

    def approve(self, packet: ApprovalReviewPacket, *, ttl: timedelta | None = None) -> ApprovalRecord:
        return self._approvals.approve(packet.approval_id, self._credential,
                                       expected_operation_hash=packet.operation_hash, ttl=ttl)

    def reject(self, approval_id: str, reason: str) -> ApprovalRecord:
        return self._approvals.reject(approval_id, self._credential, reason=reason)


class SwarmSimulator:
    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        clock: Clock | None = None,
        council: EthicsCouncil | None = None,
        policy: SecurityPolicy = DEFAULT_POLICY,
        decision_engine: DecisionEngine | None = None,
        approval_ttl: timedelta = timedelta(minutes=15),
        approval_signing_key: bytes | None = None,
    ) -> None:
        self.clock = clock or SteppingClock()
        self.db = Database(db_path)
        self.repos = Repositories(self.db)
        self.monitor = WorkflowMonitor(self.db, self.clock, self.repos)
        self.workspace = SharedWorkspace(self.db, self.monitor, self.clock, self.repos)
        self.graph = WorkflowGraphService(self.monitor)
        self.identities = IdentityRegistry()
        self.approvals = ApprovalManager(self.db, self.monitor, self.clock, self.identities,
                                         signing_key=approval_signing_key, default_ttl=approval_ttl, repos=self.repos)
        self.policy_engine = PolicyEngine(policy)
        self.gateway = CapabilityGateway(
            db=self.db, identities=self.identities, policy_engine=self.policy_engine, monitor=self.monitor,
            workspace=self.workspace, approvals=self.approvals, clock=self.clock, council=council,
            decision_engine=decision_engine,
        )
        self.coordinator = CoordinatorAgent(*self._register(COORDINATOR_ID, AgentRole.COORDINATOR, "Agent A: Coordinator"))
        self.research = ResearchAgent(*self._register(RESEARCH_ID, AgentRole.RESEARCH, "Agent B: Research"))
        self.operations = OperationsAgent(*self._register(OPERATIONS_ID, AgentRole.OPERATIONS, "Agent C: Operations"))
        self.identities.seal()
        self.operator = SimulatedHumanOperator(HUMAN_OPERATOR_ID, OPERATOR_EMAIL,
                                               self.approvals.register_approver(HUMAN_OPERATOR_ID), self.approvals)
        self.approvals.seal()
        self._seed()

    @property
    def agents(self) -> dict[str, CoordinatorAgent | ResearchAgent | OperationsAgent]:
        return {a.agent_id: a for a in (self.coordinator, self.research, self.operations)}

    def agent_profile(self, agent_id: str) -> dict[str, Any]:
        """Trusted view of an agent's identity, role, permissions, and workflows."""
        identity = self.identities.get(agent_id)
        if identity is None:
            raise KeyError(agent_id)
        ctx = self.gateway.trusted_context(identity)
        return {"agent_id": ctx.agent_id, "role": ctx.role.value, "display_name": identity.display_name,
                "permissions": sorted(ctx.permissions), "authorized_workflows": sorted(ctx.authorized_workflows)}

    def _register(self, agent_id: str, role: AgentRole, name: str) -> tuple[AgentIdentity, AgentGatewayClient]:
        identity = AgentIdentity(agent_id=agent_id, role=role, display_name=name)
        return identity, self.gateway.client_for(self.identities.register(identity))

    def _seed(self) -> None:
        if self.workspace.get("cust_001") is not None:
            return
        for cid, content in SEED_CUSTOMERS.items():
            self.workspace.create(ResourceType.CUSTOMER_RECORD, content, agent_id=SIMULATOR_ACTOR,
                                  event_workflow_id="setup", resource_workflow_id=None,
                                  acl=ResourceACL(read=[RESEARCH_ID, OPERATIONS_ID], write=[]), resource_id=cid)
        self.workspace.create(ResourceType.DOCUMENT,
                              {"title": "Service FAQ", "content": "Requests are acknowledged within one business day."},
                              agent_id=SIMULATOR_ACTOR, event_workflow_id="setup", resource_workflow_id=None,
                              acl=ResourceACL(read=["*"], write=[]), resource_id="doc_service_faq")
        self.workspace.create(ResourceType.DOCUMENT,
                              {"title": "Security policy (reference copy)",
                               "content": "Agents may not alter security policy or grant approvals."},
                              agent_id=SIMULATOR_ACTOR, event_workflow_id="setup", resource_workflow_id=None,
                              acl=ResourceACL(read=["*"], write=[]), resource_id="doc_security_policy")

    def start_workflow(self, workflow_id: str, title: str, participants: list[str] | None = None,
                       objective: str = "") -> WorkflowRecord:
        record = self.monitor.create_workflow(workflow_id, title,
                                              participants or [COORDINATOR_ID, RESEARCH_ID, OPERATIONS_ID],
                                              created_by=SIMULATOR_ACTOR, metadata={"objective": objective})
        self.workspace.create(ResourceType.WORKFLOW_METADATA, {"title": title, "objective": objective},
                              agent_id=SIMULATOR_ACTOR, event_workflow_id=workflow_id, resource_workflow_id=workflow_id,
                              acl=ResourceACL(read=["*"], write=[]), resource_id=f"meta_{workflow_id}")
        self.workspace.create(ResourceType.DOCUMENT, {"title": f"Customer brief for {workflow_id}", "content": ""},
                              agent_id=SIMULATOR_ACTOR, event_workflow_id=workflow_id, resource_workflow_id=workflow_id,
                              acl=ResourceACL(read=["*"], write=[RESEARCH_ID]), resource_id=brief_document_id(workflow_id))
        return record

    def sent_emails(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        return self.repos.sent_emails.list(workflow_id)

    def close(self) -> None:
        self.db.close()


def brief_document_id(workflow_id: str) -> str:
    return f"brief_{workflow_id}"
