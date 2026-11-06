"""Simulated tools operating only on synthetic local data.

This module has no networking code. ``send_email`` only writes a row to the
local ``sent_emails`` table. It never opens a socket, never contacts SMTP, and
never calls an email API.

Execution boundary
------------------
* Tools run only through ``MockToolExecutor.execute``. The executor refuses to
  run without an ``ExecutionPermit`` whose HMAC was made with a key that exists
  only inside the capability gateway's executor instance.
* Every tool calls ``ctx.verify(...)`` first, which re-checks the permit and the
  parameter type, so a hand-built ``ToolContext`` is useless without the key.
* Protected execution re-validates resource version, content, and approval
  inside one SQLite transaction. Validation in the gateway and in the tool
  therefore cannot drift apart.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from swarmguard.agents.workspace import SharedWorkspace
from swarmguard.clock import Clock
from swarmguard.models.actions import (
    ActionRequest,
    AssignTaskParams,
    CompleteTaskParams,
    ModifyEmailParams,
    NoParams,
    PrepareEmailParams,
    RequestApprovalParams,
    SendEmailParams,
    UpdateSharedDocumentParams,
)
from swarmguard.models.approvals import canonical_json
from swarmguard.models.events import EventType
from swarmguard.models.workflows import ResourceACL, ResourceType
from swarmguard.security.approval_manager import ApprovalManager
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories

SIMULATED_SEND_STATUS = "SIMULATED_EMAIL_SENT"
EMAIL_FIELDS = ("recipient", "subject", "body")
TOOL_ACTOR = "swarmguard.mock_tools"


class PermitError(PermissionError):
    """Execution attempted without a valid gateway-issued permit."""


class PreconditionFailed(RuntimeError):
    """Trusted state changed between policy validation and execution."""


@dataclass(frozen=True)
class ExecutionPermit:
    action_id: str
    tool: str
    expected: dict[str, Any] = field(default_factory=dict)
    mac: str = ""


@dataclass(frozen=True)
class ToolContext:
    request: ActionRequest
    permit: ExecutionPermit
    workspace: SharedWorkspace
    approvals: ApprovalManager
    monitor: WorkflowMonitor
    repos: Repositories
    db: Database
    clock: Clock
    _verifier: Callable[[ActionRequest, ExecutionPermit], None]

    def verify(self, params: BaseModel, expected_type: type[BaseModel]) -> None:
        self._verifier(self.request, self.permit)
        if not isinstance(params, expected_type):
            raise PermitError(f"{self.request.tool} received {type(params).__name__}, expected {expected_type.__name__}")


def _require_version(ctx: ToolContext) -> int:
    version = ctx.request.resource_version
    if version is None or version != ctx.permit.expected.get("resource_version"):
        raise PreconditionFailed("resource version missing or inconsistent with the authorized request")
    return version


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def read_document(ctx: ToolContext, params: NoParams) -> dict[str, Any]:
    ctx.verify(params, NoParams)
    return _read(ctx)


def read_customer_record(ctx: ToolContext, params: NoParams) -> dict[str, Any]:
    ctx.verify(params, NoParams)
    return _read(ctx)


def read_email_draft(ctx: ToolContext, params: NoParams) -> dict[str, Any]:
    ctx.verify(params, NoParams)
    return _read(ctx)


def update_shared_document(ctx: ToolContext, params: UpdateSharedDocumentParams) -> dict[str, Any]:
    ctx.verify(params, UpdateSharedDocumentParams)
    resource, diff, _ = ctx.workspace.update(
        str(ctx.request.resource_id), {"content": params.content}, agent_id=ctx.request.agent_id,
        expected_version=_require_version(ctx), event_workflow_id=ctx.request.workflow_id,
        action_id=ctx.request.action_id, tool=ctx.request.tool,
    )
    return {"status": "UPDATED" if diff else "UNCHANGED", "resource_id": resource.resource_id,
            "version": resource.version, "changed": sorted(diff)}


def prepare_email(ctx: ToolContext, params: PrepareEmailParams) -> dict[str, Any]:
    ctx.verify(params, PrepareEmailParams)
    draft, _ = ctx.workspace.create(
        ResourceType.EMAIL_DRAFT, params.fields(), agent_id=ctx.request.agent_id,
        event_workflow_id=ctx.request.workflow_id, resource_workflow_id=ctx.request.workflow_id,
        acl=ResourceACL(read=["*"], write=["*"]), action_id=ctx.request.action_id, tool=ctx.request.tool,
    )
    return {"status": "DRAFT_CREATED", "resource_id": draft.resource_id, "version": draft.version, **draft.content}


def modify_email(ctx: ToolContext, params: ModifyEmailParams) -> dict[str, Any]:
    ctx.verify(params, ModifyEmailParams)
    with ctx.db.transaction():
        draft, diff, event = ctx.workspace.update(
            str(ctx.request.resource_id), params.changes(), agent_id=ctx.request.agent_id,
            expected_version=_require_version(ctx), event_workflow_id=ctx.request.workflow_id,
            action_id=ctx.request.action_id, tool=ctx.request.tool,
        )
        invalidated = []
        if diff:
            invalidated = ctx.approvals.invalidate_for_resource(
                draft.resource_id, agent_id=ctx.request.agent_id, action_id=ctx.request.action_id,
                workflow_id=ctx.request.workflow_id, changed_fields=sorted(diff),
                cause_event_id=event.event_id if event else None, new_version=draft.version,
            )
    return {"status": "DRAFT_MODIFIED" if diff else "UNCHANGED", "resource_id": draft.resource_id,
            "version": draft.version, "changed": sorted(diff),
            "invalidated_approvals": [a.approval_id for a in invalidated]}


def request_approval(ctx: ToolContext, params: RequestApprovalParams) -> dict[str, Any]:
    ctx.verify(params, RequestApprovalParams)
    with ctx.db.transaction():
        draft = ctx.workspace.get(str(ctx.request.resource_id))
        if draft is None or draft.version != _require_version(ctx):
            raise PreconditionFailed("draft changed before the approval request was recorded")
        # Bind to the TRUSTED stored content of the exact version, not to agent-typed values.
        record = ctx.approvals.create_request(
            workflow_id=ctx.request.workflow_id, operation=params.operation, resource=draft,
            parameters={k: draft.content[k] for k in EMAIL_FIELDS}, requested_by=ctx.request.agent_id,
            requesting_action_id=ctx.request.action_id,
        )
    return {"status": record.status.value, "approval_id": record.approval_id,
            "resource_id": record.resource_id, "resource_version": record.resource_version}


def send_email(ctx: ToolContext, params: SendEmailParams) -> dict[str, Any]:
    """Record a simulated send. Steps 1-5 run in ONE transaction; any failure rolls back."""
    ctx.verify(params, SendEmailParams)
    approval_id = ctx.request.approval_id
    if approval_id is None:
        raise PreconditionFailed("protected execution without an approval reference")
    with ctx.db.transaction():
        # 1. resource version
        draft = ctx.workspace.get(str(ctx.request.resource_id))
        if draft is None or draft.version != _require_version(ctx):
            raise PreconditionFailed("draft version changed between validation and execution")
        if any(draft.content.get(k) != params.fields()[k] for k in EMAIL_FIELDS):
            raise PreconditionFailed("draft content differs from the authorized parameters")
        # 2 + 3. approval validity and single-use consumption (conditional UPDATE)
        ctx.approvals.consume(approval_id, action_id=ctx.request.action_id, workflow_id=ctx.request.workflow_id,
                              expected_operation_hash=str(ctx.permit.expected.get("operation_hash")))
        # 4. simulated execution: nothing leaves this process
        email_id = ctx.db.next_id("sent")
        ctx.repos.sent_emails.insert(
            email_id=email_id, action_id=ctx.request.action_id, workflow_id=ctx.request.workflow_id,
            approval_id=approval_id, resource_id=draft.resource_id, resource_version=draft.version,
            recipient=params.recipient, subject=params.subject, body=params.body, status=SIMULATED_SEND_STATUS,
            sent_at=ctx.clock.now(),
        )
        # 5. persist the result
        ctx.monitor.record_event(
            EventType.EMAIL_SENT_SIMULATED, ctx.request.workflow_id, agent_id=TOOL_ACTOR,
            action_id=ctx.request.action_id, tool="send_email", resource_id=draft.resource_id,
            resource_version_before=draft.version, resource_version_after=draft.version, approval_id=approval_id,
            summary=f"Simulated email {email_id} recorded for {params.recipient} (no real delivery).",
            details={"email_id": email_id, "recipient": params.recipient, "subject": params.subject},
        )
    return {"status": SIMULATED_SEND_STATUS, "email_id": email_id, "recipient": params.recipient,
            "resource_id": draft.resource_id, "resource_version": draft.version, "real_delivery": False}


def assign_task(ctx: ToolContext, params: AssignTaskParams) -> dict[str, Any]:
    ctx.verify(params, AssignTaskParams)
    with ctx.db.transaction():
        task, created = ctx.workspace.create(
            ResourceType.TASK,
            {"assignee": params.assignee, "assigned_by": ctx.request.agent_id, "description": params.description,
             "depends_on_task_ids": list(params.depends_on_task_ids), "status": "ASSIGNED"},
            agent_id=ctx.request.agent_id, event_workflow_id=ctx.request.workflow_id,
            resource_workflow_id=ctx.request.workflow_id, acl=ResourceACL(read=["*"], write=[params.assignee]),
            action_id=ctx.request.action_id, tool=ctx.request.tool,
        )
        ctx.monitor.record_event(
            EventType.TASK_ASSIGNED, ctx.request.workflow_id, agent_id=ctx.request.agent_id,
            action_id=ctx.request.action_id, tool=ctx.request.tool, resource_id=task.resource_id,
            resource_version_after=1,
            summary=f"{ctx.request.agent_id} assigned {task.resource_id} to {params.assignee}: {params.description}"
            + (f" (depends on {', '.join(params.depends_on_task_ids)})" if params.depends_on_task_ids else ""),
            details={"assignee": params.assignee, "depends_on_task_ids": list(params.depends_on_task_ids)},
        )
    return {"status": "TASK_ASSIGNED", "resource_id": task.resource_id, "version": 1, "assignee": params.assignee}


def complete_task(ctx: ToolContext, params: CompleteTaskParams) -> dict[str, Any]:
    ctx.verify(params, CompleteTaskParams)
    with ctx.db.transaction():
        task, _, _ = ctx.workspace.update(
            str(ctx.request.resource_id), {"status": "COMPLETED", "summary": params.summary},
            agent_id=ctx.request.agent_id, expected_version=_require_version(ctx),
            event_workflow_id=ctx.request.workflow_id, action_id=ctx.request.action_id, tool=ctx.request.tool,
        )
        ctx.monitor.record_event(
            EventType.TASK_COMPLETED, ctx.request.workflow_id, agent_id=ctx.request.agent_id,
            action_id=ctx.request.action_id, tool=ctx.request.tool, resource_id=task.resource_id,
            resource_version_after=task.version, summary=f"{ctx.request.agent_id} completed {task.resource_id}.",
        )
    return {"status": "TASK_COMPLETED", "resource_id": task.resource_id, "version": task.version}


def _read(ctx: ToolContext) -> dict[str, Any]:
    resource, _ = ctx.workspace.read(
        str(ctx.request.resource_id), agent_id=ctx.request.agent_id, event_workflow_id=ctx.request.workflow_id,
        tool=ctx.request.tool, action_id=ctx.request.action_id,
    )
    if ctx.request.resource_version is not None and ctx.request.resource_version != resource.version:
        raise PreconditionFailed("resource version changed before the read")
    return {"status": "OK", "resource_id": resource.resource_id, "version": resource.version,
            "content": dict(resource.content)}


_TOOL_FUNCTIONS: dict[str, Callable[[ToolContext, Any], dict[str, Any]]] = {
    "read_document": read_document,
    "update_shared_document": update_shared_document,
    "read_customer_record": read_customer_record,
    "read_email_draft": read_email_draft,
    "prepare_email": prepare_email,
    "modify_email": modify_email,
    "send_email": send_email,
    "request_approval": request_approval,
    "assign_task": assign_task,
    "complete_task": complete_task,
}
REGISTERED_TOOL_NAMES = frozenset(_TOOL_FUNCTIONS)


def _permit_mac(key: bytes, action_id: str, tool: str, expected: dict[str, Any]) -> str:
    message = canonical_json({"action_id": action_id, "tool": tool, "expected": expected})
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


class MockToolExecutor:
    """Runs tools only with a valid, single-use, gateway-issued permit. Owned by the gateway."""

    def __init__(self, *, permit_key: bytes, workspace: SharedWorkspace, approvals: ApprovalManager,
                 monitor: WorkflowMonitor, repos: Repositories, db: Database, clock: Clock) -> None:
        if len(permit_key) < 32:
            raise ValueError("permit key must be at least 32 bytes")
        self._key = permit_key
        self._workspace = workspace
        self._approvals = approvals
        self._monitor = monitor
        self._repos = repos
        self._db = db
        self._clock = clock
        self._used: set[str] = set()

    def issue_permit(self, action_id: str, tool: str, expected: dict[str, Any]) -> ExecutionPermit:
        return ExecutionPermit(action_id, tool, dict(expected), _permit_mac(self._key, action_id, tool, expected))

    def _verify(self, request: ActionRequest, permit: ExecutionPermit) -> None:
        if not hmac.compare_digest(_permit_mac(self._key, permit.action_id, permit.tool, permit.expected), permit.mac):
            raise PermitError("invalid execution permit")
        if permit.action_id != request.action_id or permit.tool != request.tool:
            raise PermitError("permit does not match the request")

    def execute(self, request: ActionRequest, params: BaseModel, permit: ExecutionPermit) -> dict[str, Any]:
        self._verify(request, permit)
        if permit.action_id in self._used:
            raise PermitError("permit already used")
        func = _TOOL_FUNCTIONS.get(request.tool)
        if func is None:
            raise PermitError(f"no implementation for tool {request.tool}")
        self._used.add(permit.action_id)
        ctx = ToolContext(request=request, permit=permit, workspace=self._workspace, approvals=self._approvals,
                          monitor=self._monitor, repos=self._repos, db=self._db, clock=self._clock,
                          _verifier=self._verify)
        return func(ctx, params)
