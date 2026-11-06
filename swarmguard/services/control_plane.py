"""Trusted control plane (Stage 7): administration, human approval and incident controls.

Used by the authenticated console (role-checked routes) and the local host CLI. Agents never reach it:
it is not on the agent network and has no agent-facing interface. Approver credentials exist only here;
gateway and executor know approver *IDs* (to validate stored approvals) but cannot grant approvals.
Every mutating method records an event in the chained ``system_security`` stream.
"""
from __future__ import annotations
from datetime import timedelta
from typing import Any
from swarmguard.agents.simulator import SEED_CUSTOMERS, brief_document_id
from swarmguard.agents.workspace import SharedWorkspace
from swarmguard.audit.verification import VerificationReport, load_checkpoints, verify_chain
from swarmguard.authentication.service_identity import IdentityAdministration, assert_disjoint_approvers
from swarmguard.clock import Clock
from swarmguard.models.events import ActorType, EventType
from swarmguard.models.workflows import ResourceACL, ResourceType, WorkflowStatus
from swarmguard.security.approval_manager import ApprovalError, ApprovalManager
from swarmguard.security.identity import IdentityRegistry
from swarmguard.security.policy_engine import DEFAULT_POLICY
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import (ControlStateRepository, HeartbeatRepository, ProtectedOperationRepository,
                                             Repositories, ServiceIdentityRepository)

SECURITY_STREAM = "system_security"
CONTROL_ACTOR = "swarmguard.control"


class ControlPlane:
    def __init__(self, db: Database, clock: Clock, *, approval_signing_key: bytes, approver_ids: list[str],
                 approval_ttl: timedelta = timedelta(minutes=15)) -> None:
        self.db, self.clock = db, clock
        self.repos = Repositories(db)
        self.monitor = WorkflowMonitor(db, clock, self.repos)
        self.workspace = SharedWorkspace(db, self.monitor, clock, self.repos)
        self.approvals = ApprovalManager(db, self.monitor, clock, IdentityRegistry(), signing_key=approval_signing_key,
                                         default_ttl=approval_ttl, repos=self.repos)
        assert_disjoint_approvers(db, approver_ids)
        self._approver_credentials = {a: self.approvals.register_approver(a) for a in approver_ids}
        self.approvals.seal()
        self.identity_admin = IdentityAdministration(db)
        self.identities = ServiceIdentityRepository(db)
        self.ops = ProtectedOperationRepository(db)
        self.control = ControlStateRepository(db)
        self.heartbeats = HeartbeatRepository(db)

    @property
    def approver_ids(self) -> list[str]:
        return sorted(self._approver_credentials)

    def _audit(self, event_type: EventType, actor: str, summary: str, **details: Any) -> None:
        self.monitor.record_event(event_type, SECURITY_STREAM, agent_id=actor, actor_type=ActorType.HUMAN
                                  if actor != CONTROL_ACTOR else ActorType.SYSTEM, parent_event_ids=[],
                                  summary=summary[:500], details=details)

    # ------------------------------------------------------------------ bootstrap (trusted setup)

    def seed_synthetic_records(self) -> None:
        if self.workspace.get("cust_001") is not None:
            return
        for cid, content in SEED_CUSTOMERS.items():
            self.workspace.create(ResourceType.CUSTOMER_RECORD, content, agent_id=CONTROL_ACTOR, event_workflow_id="setup",
                                  resource_workflow_id=None, acl=ResourceACL(read=["research_agent", "operations_agent"],
                                                                             write=[]), resource_id=cid)
        self.workspace.create(ResourceType.DOCUMENT, {"title": "Service FAQ",
                                                      "content": "Requests are acknowledged within one business day."},
                              agent_id=CONTROL_ACTOR, event_workflow_id="setup", resource_workflow_id=None,
                              acl=ResourceACL(read=["*"], write=[]), resource_id="doc_service_faq")

    def create_workflow(self, workflow_id: str, title: str, participants: list[str], *, by: str,
                        purpose: str = "service_updates") -> dict[str, Any]:
        if self.monitor.get_workflow(workflow_id) is not None:
            raise ValueError("workflow already exists")
        self.monitor.create_workflow(workflow_id, title, participants, created_by=by,
                                     metadata={"objective": title, "purpose": purpose, "control_plane": True})
        self.workspace.create(ResourceType.DOCUMENT, {"title": f"Customer brief for {workflow_id}", "content": ""},
                              agent_id=CONTROL_ACTOR, event_workflow_id=workflow_id, resource_workflow_id=workflow_id,
                              acl=ResourceACL(read=["*"], write=["research_agent", "operations_agent"]),
                              resource_id=brief_document_id(workflow_id))
        self._audit(EventType.OPERATOR_ACTION, by, f"Workflow {workflow_id} created for {', '.join(participants)}.",
                    workflow_id=workflow_id)
        return self.monitor.get_workflow(workflow_id).model_dump(mode="json")  # type: ignore[union-attr]

    def pause_workflow(self, workflow_id: str, *, by: str, reason: str) -> None:
        if self.monitor.get_workflow(workflow_id) is None:
            raise KeyError(workflow_id)
        self.monitor.set_workflow_status(workflow_id, WorkflowStatus.HALTED)
        self._audit(EventType.WORKFLOW_PAUSED, by, f"Workflow {workflow_id} halted: {reason}", workflow_id=workflow_id)

    # ------------------------------------------------------------------ identities

    def register_runtime(self, runtime_id: str, description: str, *, by: str) -> dict[str, Any]:
        record = self.identity_admin.register_runtime(runtime_id, description, by=by)
        self._audit(EventType.IDENTITY_REGISTERED, by, f"Agent runtime {runtime_id} registered.", runtime_id=runtime_id)
        return record

    def bind_agent(self, agent_id: str, runtime_id: str, role: str, workflows: list[str],
                   capabilities: list[str] | None, *, by: str) -> dict[str, Any]:
        if agent_id in self._approver_credentials:
            raise ValueError("an approver identity cannot be bound as an agent")
        record = self.identity_admin.bind_agent(agent_id, runtime_id, role, workflows, capabilities, by=by)
        self._audit(EventType.IDENTITY_REGISTERED, by, f"Agent {agent_id} bound to runtime {runtime_id} as {role}.",
                    agent_id=agent_id, runtime_id=runtime_id, capabilities=record["capabilities"])
        return record

    def revoke_identity(self, identity_id: str, *, by: str, reason: str) -> dict[str, Any]:
        """Revoke a runtime (and all its agents), an agent binding, or a credential ID (``cred_...``)."""
        now = self.clock.now().isoformat()
        if identity_id.startswith("cred_"):
            changed, kind = self.identities.revoke_credential(identity_id, by=by, reason=reason, at=now), "credential"
        elif self.identities.runtime(identity_id) is not None:
            changed, kind = self.identities.revoke_runtime(identity_id, by=by, reason=reason, at=now), "runtime"
        elif self.identities.binding(identity_id) is not None:
            changed, kind = self.identities.revoke_agent(identity_id, by=by, at=now), "agent"
        else:
            raise KeyError(identity_id)
        self._audit(EventType.IDENTITY_REVOKED, by, f"{kind} {identity_id} revoked: {reason}", identity_id=identity_id,
                    kind=kind, changed=changed)
        return {"identity_id": identity_id, "kind": kind, "revoked": True, "changed": changed}

    def identity_view(self) -> dict[str, Any]:
        return {"runtimes": self.identities.runtimes(), "agents": self.identities.bindings(),
                "revoked_credentials": self.identities.revoked_credentials(),
                "approvers": self.approver_ids, "policy_version": DEFAULT_POLICY.version}

    # ------------------------------------------------------------------ approvals

    def grant_approval(self, approval_id: str, *, approver_id: str, operation_hash: str) -> dict[str, Any]:
        credential = self._approver_credentials.get(approver_id)
        if credential is None:
            raise PermissionError("account is not a registered trusted approver")
        packet = self.approvals.review_packet(approval_id)
        if packet.operation_hash != operation_hash:
            raise ApprovalError("reviewed operation does not match the stored request")
        return self.approvals.approve(approval_id, credential, expected_operation_hash=operation_hash).model_dump(mode="json")

    def reject_approval(self, approval_id: str, *, approver_id: str, reason: str) -> dict[str, Any]:
        credential = self._approver_credentials.get(approver_id)
        if credential is None:
            raise PermissionError("account is not a registered trusted approver")
        return self.approvals.reject(approval_id, credential, reason=reason).model_dump(mode="json")

    def revoke_approval(self, approval_id: str, *, approver_id: str, reason: str) -> dict[str, Any]:
        credential = self._approver_credentials.get(approver_id)
        if credential is None:
            raise PermissionError("account is not a registered trusted approver")
        return self.approvals.revoke(approval_id, credential, reason=reason).model_dump(mode="json")

    def revoke_outstanding_approvals(self, *, approver_id: str, reason: str, workflow_id: str | None = None) -> list[str]:
        revoked = []
        for record in self.repos.approvals.list():
            if record.status.value in ("PENDING", "APPROVED") and (workflow_id is None or record.workflow_id == workflow_id):
                self.revoke_approval(record.approval_id, approver_id=approver_id, reason=reason)
                revoked.append(record.approval_id)
        return revoked

    # ------------------------------------------------------------------ incident controls

    def emergency_state(self) -> dict[str, Any]:
        current = self.control.get("emergency_stop")
        return {**(current[0] if current else {"active": False}), "version": current[1] if current else 0}

    def activate_emergency_stop(self, *, by: str, reason: str) -> dict[str, Any]:
        current = self.control.get("emergency_stop")
        if current and current[0].get("active"):
            return self.emergency_state()
        value = {"active": True, "activated_by": by, "reason": reason, "activated_at": self.clock.now().isoformat()}
        self.control.put("emergency_stop", value, expected_version=current[1] if current else None, by=by,
                         at=self.clock.now().isoformat())
        self._audit(EventType.EMERGENCY_STOP_ACTIVATED, by, f"Emergency stop activated: {reason}", reason=reason)
        return self.emergency_state()

    def recover_emergency_stop(self, *, by: str, reason: str, confirm: str) -> dict[str, Any]:
        if confirm != "RESUME PROTECTED OPERATIONS":
            raise ValueError("explicit recovery confirmation phrase required")
        current = self.control.get("emergency_stop")
        if not current or not current[0].get("active"):
            return self.emergency_state()
        value = {**current[0], "active": False, "recovered_by": by, "recovery_reason": reason,
                 "recovered_at": self.clock.now().isoformat()}
        self.control.put("emergency_stop", value, expected_version=current[1], by=by, at=self.clock.now().isoformat())
        self._audit(EventType.EMERGENCY_STOP_RECOVERED, by, f"Emergency stop cleared: {reason}", reason=reason)
        return self.emergency_state()

    def set_capability(self, tool: str, *, enabled: bool, by: str, reason: str) -> dict[str, Any]:
        if tool not in DEFAULT_POLICY.tools:
            raise KeyError(tool)
        current = self.control.get("disabled_capabilities")
        tools = set(current[0].get("tools", [])) if current else set()
        tools = tools - {tool} if enabled else tools | {tool}
        self.control.put("disabled_capabilities", {"tools": sorted(tools)}, expected_version=current[1] if current else None,
                         by=by, at=self.clock.now().isoformat())
        self._audit(EventType.CAPABILITY_STATE_CHANGED, by, f"Capability {tool} {'enabled' if enabled else 'disabled'}: "
                    f"{reason}", tool=tool, enabled=enabled)
        return {"disabled_capabilities": sorted(tools)}

    # ------------------------------------------------------------------ observability (read-only)

    def verify_audit(self, checkpoint_dir: Any = None) -> VerificationReport:
        return verify_chain(self.db, load_checkpoints(checkpoint_dir))

    def security_events(self, limit: int = 200) -> list[dict[str, Any]]:
        events = [e for e in self.repos.events.query() if e.workflow_id == SECURITY_STREAM
                  or e.event_type in (EventType.SECURITY_ALERT, EventType.APPROVAL_REVOKED)]
        return [e.model_dump(mode="json") for e in events[-limit:]][::-1]

    def operation_detail(self, operation_id: str) -> dict[str, Any] | None:
        op = self.ops.get(operation_id)
        if op is None:
            return None
        events = [e.model_dump(mode="json") for e in self.repos.events.query(action_id=op["action_id"])] \
            if op.get("action_id") else []
        decision = self.monitor.get_decision_for_action(op["action_id"]) if op.get("action_id") else None
        action = self.monitor.get_action(op["action_id"]) if op.get("action_id") else None
        return {**op, "transitions": self.ops.transitions(operation_id), "events": events,
                "decision": decision.model_dump(mode="json") if decision else None,
                "approval_id": action.approval_id if action else None}

    def status(self) -> dict[str, Any]:
        decisions = self.db.fetchall("SELECT decision, COUNT(*) FROM decisions GROUP BY decision")
        auth_failures = self.db.fetchone("SELECT COUNT(*) FROM events WHERE event_type = ?",
                                         (EventType.AUTHENTICATION_FAILED.value,))
        alerts = self.db.fetchone("SELECT COUNT(*) FROM events WHERE event_type = ?", (EventType.SECURITY_ALERT.value,))
        sent = self.db.fetchone("SELECT COUNT(*) FROM sent_emails")
        active = self.db.fetchone("SELECT COUNT(*) FROM workflows WHERE status = 'ACTIVE'")
        pending = self.db.fetchone("SELECT COUNT(*) FROM approvals WHERE status = 'PENDING'")
        return {"emergency_stop": self.emergency_state(),
                "disabled_capabilities": (self.control.get("disabled_capabilities") or ({"tools": []}, 0))[0]["tools"],
                "decisions": {str(r[0]): int(r[1]) for r in decisions}, "authentication_failures": int(auth_failures[0]),
                "security_alerts": int(alerts[0]), "operations": self.ops.counts(),
                "protected_executions": {"simulated_emails_sent": int(sent[0])},
                "active_workflows": int(active[0]), "pending_approvals": int(pending[0]),
                "note": "Blocked/escalated decisions are proposed violations; simulated sends are executed effects."}
