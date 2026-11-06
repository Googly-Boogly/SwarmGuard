"""Human-approval models and canonical binding.

Two digests are used:

* ``operation_hash``: computed when approval is *requested*. It covers the exact
  operation the human will review: approval ID, workflow, operation, resource ID
  and version, and parameters.
* ``binding_hash``: computed when approval is *granted*. It covers the
  operation hash plus the trusted approver identity and expiration timestamp.

Digests are integrity-binding values, not proof that a human approved anything.
Approval authority comes from the trusted approval store and from the
approver's credential at grant time.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


def canonical_json(value: Any) -> str:
    """Deterministic serialization (sorted keys, compact separators, UTF-8 preserved)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OperationSnapshot(BaseModel):
    """Exactly what the human reviews and what a later execution must match."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    workflow_id: str
    operation: str
    resource_id: str
    resource_version: int
    parameters: dict[str, Any]

    def digest(self) -> str:
        return sha256_hex(canonical_json(self.model_dump(mode="json")))


class ApprovalBinding(BaseModel):
    """Full binding sealed at grant time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_hash: str
    approver_id: str
    expires_at: str  # ISO-8601 UTC, kept as text so serialization is byte-stable

    def digest(self) -> str:
        return sha256_hex(canonical_json(self.model_dump(mode="json")))


class ApprovalRecord(BaseModel):
    """Boundary B: trusted approval state, written only by the ApprovalManager."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    workflow_id: str
    operation: str
    resource_id: str
    resource_version: int
    approved_parameters: dict[str, Any]
    operation_hash: str
    binding_hash: str | None = None
    signature: str | None = None
    requested_by: str
    requesting_action_id: str | None = None
    status: ApprovalStatus
    approver_id: str | None = None
    status_reason: str = ""
    created_at: datetime
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    consumed_by_action_id: str | None = None

    def snapshot(self) -> OperationSnapshot:
        return OperationSnapshot(
            approval_id=self.approval_id,
            workflow_id=self.workflow_id,
            operation=self.operation,
            resource_id=self.resource_id,
            resource_version=self.resource_version,
            parameters=self.approved_parameters,
        )


class ApprovalReviewPacket(BaseModel):
    """What the trusted human operator is shown before deciding."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    workflow_id: str
    operation: str
    requested_by: str
    resource_id: str
    resource_version: int
    parameters: dict[str, Any]
    operation_hash: str = Field(description="SHA-256 of the canonical operation snapshot.")
