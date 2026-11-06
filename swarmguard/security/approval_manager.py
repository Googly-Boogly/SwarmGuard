"""Simulated human-approval management (Boundary B).

Invariants
----------
* Requests are always created PENDING and snapshot the *trusted* stored
  resource version; the requesting agent cannot choose what gets bound.
* Only a registered human-operator ``ApproverCredential`` can approve or reject.
  AI agent IDs cannot be registered as approvers, and agent credentials are a
  different type.
* At request time: ``operation_hash`` = SHA-256(canonical{approval_id, workflow,
  operation, resource_id, resource_version, parameters}).
  At grant time: ``binding_hash`` = SHA-256(canonical{operation_hash, approver_id,
  expires_at}), plus ``signature`` = HMAC-SHA256(signing_key, binding_hash).
  Digests are integrity bindings, not proof of approval. Authority comes from
  the trusted record together with the approver credential at grant time.
* Approvals expire, are single-use (atomic conditional UPDATE to CONSUMED), and
  are INVALIDATED when the bound resource changes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta
from typing import Any

from swarmguard.clock import Clock
from swarmguard.models.actions import ID_PATTERN
from swarmguard.models.approvals import (
    ApprovalBinding,
    ApprovalRecord,
    ApprovalReviewPacket,
    ApprovalStatus,
    OperationSnapshot,
)
from swarmguard.models.events import ActorType, EventType
from swarmguard.models.workflows import SharedResource
from swarmguard.security.identity import IdentityRegistry
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories

logger = logging.getLogger("swarmguard.approvals")

DEFAULT_APPROVAL_TTL = timedelta(minutes=15)
MANAGER_ACTOR = "swarmguard.approval_manager"


class ApprovalError(RuntimeError):
    pass


class UnauthorizedApproverError(ApprovalError):
    pass


class ApproverCredential:
    """Opaque credential held only by the trusted (simulated) human operator."""

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "ApproverCredential(<redacted>)"

    def __reduce__(self):  # pragma: no cover - defensive
        raise TypeError("credentials are not serializable")


class ApprovalManager:
    def __init__(
        self,
        db: Database,
        monitor: WorkflowMonitor,
        clock: Clock,
        agent_registry: IdentityRegistry,
        *,
        signing_key: bytes | None = None,
        default_ttl: timedelta = DEFAULT_APPROVAL_TTL,
        repos: Repositories | None = None,
    ) -> None:
        self._db = db
        self._monitor = monitor
        self._clock = clock
        self._agents = agent_registry
        self._repos = repos or monitor.repos
        self._ttl = default_ttl
        self._key = signing_key or secrets.token_bytes(32)
        if len(self._key) < 32:
            raise ValueError("approval signing key must be at least 32 bytes")
        self._approvers: dict[str, str] = {}  # token -> approver_id
        self._sealed = False

    # ------------------------------------------------------------------ approvers

    def register_approver(self, approver_id: str) -> ApproverCredential:
        if self._sealed:
            raise ApprovalError("approver registry is sealed")
        if not re.fullmatch(ID_PATTERN, approver_id):
            raise ValueError("invalid approver id")
        if self._agents.is_agent(approver_id):
            raise UnauthorizedApproverError("AI agents cannot be registered as approvers")
        if approver_id in self._approvers.values():
            raise ValueError("approver already registered")
        token = secrets.token_urlsafe(32)
        self._approvers[token] = approver_id
        return ApproverCredential(token)

    def seal(self) -> None:
        self._sealed = True

    def is_trusted_approver(self, approver_id: str | None) -> bool:
        return approver_id is not None and approver_id in self._approvers.values()

    def _resolve_approver(self, credential: object) -> str:
        if isinstance(credential, ApproverCredential):
            for token, approver_id in self._approvers.items():
                if secrets.compare_digest(token, credential.token):
                    return approver_id
        raise UnauthorizedApproverError("credential is not a registered human-approver credential")

    # ------------------------------------------------------------------ binding

    @staticmethod
    def compute_operation_hash(
        *, approval_id: str, workflow_id: str, operation: str, resource_id: str, resource_version: int,
        parameters: dict[str, Any],
    ) -> str:
        return OperationSnapshot(
            approval_id=approval_id, workflow_id=workflow_id, operation=operation, resource_id=resource_id,
            resource_version=resource_version, parameters=parameters,
        ).digest()

    def _sign(self, binding_hash: str) -> str:
        return hmac.new(self._key, binding_hash.encode("ascii"), hashlib.sha256).hexdigest()

    def verify_integrity(self, record: ApprovalRecord) -> bool:
        """Recompute every digest from stored fields; check the signature of granted approvals."""
        if not hmac.compare_digest(record.snapshot().digest(), record.operation_hash):
            return False
        if record.binding_hash is None:
            return record.status in (ApprovalStatus.PENDING, ApprovalStatus.REJECTED, ApprovalStatus.INVALIDATED)
        if record.approver_id is None or record.expires_at is None or record.signature is None:
            return False
        binding = ApprovalBinding(operation_hash=record.operation_hash, approver_id=record.approver_id,
                                  expires_at=record.expires_at.isoformat())
        return hmac.compare_digest(binding.digest(), record.binding_hash) and hmac.compare_digest(
            self._sign(record.binding_hash), record.signature
        )

    # ------------------------------------------------------------------ lifecycle

    def create_request(
        self, *, workflow_id: str, operation: str, resource: SharedResource, parameters: dict[str, Any],
        requested_by: str, requesting_action_id: str | None,
    ) -> ApprovalRecord:
        """Create a PENDING request bound to the given trusted resource snapshot."""
        with self._db.transaction():
            approval_id = self._db.next_id("approval")
            record = ApprovalRecord(
                approval_id=approval_id, workflow_id=workflow_id, operation=operation,
                resource_id=resource.resource_id, resource_version=resource.version, approved_parameters=parameters,
                operation_hash=self.compute_operation_hash(
                    approval_id=approval_id, workflow_id=workflow_id, operation=operation,
                    resource_id=resource.resource_id, resource_version=resource.version, parameters=parameters,
                ),
                requested_by=requested_by, requesting_action_id=requesting_action_id, status=ApprovalStatus.PENDING,
                created_at=self._clock.now(),
            )
            self._repos.approvals.insert(record)
            self._monitor.record_event(
                EventType.APPROVAL_REQUESTED, workflow_id, agent_id=requested_by, action_id=requesting_action_id,
                tool=operation, resource_id=resource.resource_id, resource_version_before=resource.version,
                resource_version_after=resource.version, approval_id=approval_id,
                summary=f"{requested_by} requested human approval {approval_id} for {operation} of "
                f"{resource.resource_id} v{resource.version}.",
                details={"parameters": parameters, "operation_hash": record.operation_hash},
            )
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._repos.approvals.get(approval_id)

    def list_for_workflow(self, workflow_id: str) -> list[ApprovalRecord]:
        return self._repos.approvals.list(workflow_id=workflow_id)

    def pending(self) -> list[ApprovalRecord]:
        return self._repos.approvals.list(statuses=(ApprovalStatus.PENDING,))

    def review_packet(self, approval_id: str) -> ApprovalReviewPacket:
        r = self._require(approval_id)
        return ApprovalReviewPacket(
            approval_id=r.approval_id, workflow_id=r.workflow_id, operation=r.operation, requested_by=r.requested_by,
            resource_id=r.resource_id, resource_version=r.resource_version, parameters=r.approved_parameters,
            operation_hash=r.operation_hash,
        )

    def approve(self, approval_id: str, credential: object, *, expected_operation_hash: str,
                ttl: timedelta | None = None) -> ApprovalRecord:
        """Approve exactly the reviewed snapshot (``expected_operation_hash``)."""
        approver_id = self._authorize(approval_id, credential, "approve")
        now = self._clock.now()
        with self._db.transaction():
            record = self._require(approval_id)
            if record.status is not ApprovalStatus.PENDING:
                raise ApprovalError(f"{approval_id} is {record.status.value}, not PENDING")
            if not hmac.compare_digest(record.operation_hash, expected_operation_hash):
                raise ApprovalError("reviewed operation does not match the stored request")
            if not self.verify_integrity(record):
                raise ApprovalError("approval record failed integrity check")
            expires_at = now + (ttl or self._ttl)
            binding_hash = ApprovalBinding(operation_hash=record.operation_hash, approver_id=approver_id,
                                           expires_at=expires_at.isoformat()).digest()
            if not self._repos.approvals.grant(approval_id, approver_id=approver_id, decided_at=now,
                                               expires_at=expires_at, binding_hash=binding_hash,
                                               signature=self._sign(binding_hash)):
                raise ApprovalError(f"{approval_id} changed concurrently")
            self._monitor.record_event(
                EventType.APPROVAL_GRANTED, record.workflow_id, agent_id=approver_id, actor_type=ActorType.HUMAN,
                tool=record.operation, resource_id=record.resource_id,
                resource_version_before=record.resource_version, resource_version_after=record.resource_version,
                approval_id=approval_id, parent_event_ids=self._approval_event_ids(approval_id, EventType.APPROVAL_REQUESTED),
                summary=f"Human operator '{approver_id}' approved {approval_id}: {record.operation} of "
                f"{record.resource_id} v{record.resource_version} (expires {expires_at.isoformat()}).",
                details={"approved_parameters": record.approved_parameters, "operation_hash": record.operation_hash,
                         "binding_hash": binding_hash, "expires_at": expires_at.isoformat()},
            )
        return self._require(approval_id)

    def reject(self, approval_id: str, credential: object, *, reason: str) -> ApprovalRecord:
        approver_id = self._authorize(approval_id, credential, "reject")
        with self._db.transaction():
            record = self._require(approval_id)
            if not self._repos.approvals.transition(
                approval_id, from_statuses=(ApprovalStatus.PENDING,), to=ApprovalStatus.REJECTED, reason=reason,
                approver_id=approver_id, decided_at=self._clock.now(),
            ):
                raise ApprovalError(f"{approval_id} is {record.status.value}, not PENDING")
            self._monitor.record_event(
                EventType.APPROVAL_REJECTED, record.workflow_id, agent_id=approver_id, actor_type=ActorType.HUMAN,
                tool=record.operation, resource_id=record.resource_id, approval_id=approval_id,
                parent_event_ids=self._approval_event_ids(approval_id, EventType.APPROVAL_REQUESTED),
                summary=f"Human operator '{approver_id}' rejected {approval_id}: {reason}",
            )
        return self._require(approval_id)

    def invalidate_for_resource(self, resource_id: str, *, agent_id: str, action_id: str | None, workflow_id: str,
                                changed_fields: list[str], cause_event_id: str | None,
                                new_version: int) -> list[ApprovalRecord]:
        """Invalidate live approvals bound to a resource that has just changed."""
        invalidated: list[ApprovalRecord] = []
        with self._db.transaction():
            for record in self._repos.approvals.list(
                resource_id=resource_id, statuses=(ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
            ):
                reason = (f"{agent_id} changed {', '.join(changed_fields)} of {resource_id} "
                          f"(v{record.resource_version} -> v{new_version}) after the approval was "
                          f"{'granted' if record.status is ApprovalStatus.APPROVED else 'requested'}")
                self._repos.approvals.transition(
                    record.approval_id, from_statuses=(ApprovalStatus.PENDING, ApprovalStatus.APPROVED),
                    to=ApprovalStatus.INVALIDATED, reason=reason,
                )
                parents = self._approval_event_ids(record.approval_id)[-1:] + ([cause_event_id] if cause_event_id else [])
                self._monitor.record_event(
                    EventType.APPROVAL_INVALIDATED, workflow_id, agent_id=MANAGER_ACTOR, action_id=action_id,
                    tool=record.operation, resource_id=resource_id, resource_version_before=record.resource_version,
                    resource_version_after=new_version, approval_id=record.approval_id, parent_event_ids=parents,
                    summary=f"Approval {record.approval_id} invalidated: {reason}.",
                    details={"changed_fields": changed_fields, "modified_by": agent_id},
                )
                invalidated.append(self._require(record.approval_id))
        return invalidated

    def expire_stale(self) -> list[ApprovalRecord]:
        now = self._clock.now()
        expired: list[ApprovalRecord] = []
        with self._db.transaction():
            for record in self._repos.approvals.list(statuses=(ApprovalStatus.APPROVED,)):
                if record.expires_at is not None and record.expires_at <= now and self._repos.approvals.transition(
                    record.approval_id, from_statuses=(ApprovalStatus.APPROVED,), to=ApprovalStatus.EXPIRED,
                    reason="approval expired",
                ):
                    self._monitor.record_event(
                        EventType.APPROVAL_EXPIRED, record.workflow_id, agent_id=MANAGER_ACTOR,
                        approval_id=record.approval_id, resource_id=record.resource_id,
                        parent_event_ids=self._approval_event_ids(record.approval_id, EventType.APPROVAL_GRANTED),
                        summary=f"Approval {record.approval_id} expired.",
                    )
                    expired.append(self._require(record.approval_id))
        return expired

    def consume(self, approval_id: str, *, action_id: str, workflow_id: str, expected_operation_hash: str) -> ApprovalRecord:
        """Atomically mark an APPROVED, unexpired, matching approval as used.

        Must run inside the same transaction as the protected execution. The
        conditional UPDATE guarantees at most one consumer, even across
        separate SQLite connections.
        """
        now = self._clock.now()
        with self._db.transaction():
            record = self._require(approval_id)
            if record.workflow_id != workflow_id:
                raise ApprovalError("approval belongs to a different workflow")
            if record.status is not ApprovalStatus.APPROVED:
                raise ApprovalError(f"{approval_id} is {record.status.value}")
            if record.expires_at is None or record.expires_at <= now:
                raise ApprovalError(f"{approval_id} has expired")
            if not hmac.compare_digest(record.operation_hash, expected_operation_hash):
                raise ApprovalError("operation changed between validation and execution")
            if not self.verify_integrity(record):
                raise ApprovalError("approval record failed integrity check")
            if not self._repos.approvals.transition(
                approval_id, from_statuses=(ApprovalStatus.APPROVED,), to=ApprovalStatus.CONSUMED,
                reason="consumed by authorized execution", consumed_by_action_id=action_id,
            ):
                raise ApprovalError(f"{approval_id} was consumed concurrently")
            head = self._monitor.head_event(action_id)
            self._monitor.record_event(
                EventType.APPROVAL_CONSUMED, record.workflow_id, agent_id=MANAGER_ACTOR, action_id=action_id,
                tool=record.operation, resource_id=record.resource_id, approval_id=approval_id,
                parent_event_ids=self._approval_event_ids(approval_id, EventType.APPROVAL_GRANTED)
                + ([head.event_id] if head else []),
                summary=f"Approval {approval_id} consumed by {action_id} (single use).",
            )
        return self._require(approval_id)

    # ------------------------------------------------------------------ internals

    def _approval_event_ids(self, approval_id: str, event_type: EventType | None = None) -> list[str]:
        return [e.event_id for e in self._monitor.find_events(approval_id=approval_id, event_type=event_type)]

    def _authorize(self, approval_id: str, credential: object, verb: str) -> str:
        try:
            return self._resolve_approver(credential)
        except UnauthorizedApproverError:
            record = self.get(approval_id)
            self._monitor.record_event(
                EventType.SECURITY_ALERT, record.workflow_id if record else "unknown", agent_id=MANAGER_ACTOR,
                approval_id=approval_id, reason_code="UNTRUSTED_APPROVER",
                summary=f"Rejected attempt to {verb} {approval_id} without a human-approver credential.",
            )
            raise

    def _require(self, approval_id: str) -> ApprovalRecord:
        record = self.get(approval_id)
        if record is None:
            raise ApprovalError(f"unknown approval {approval_id}")
        return record

