"""Deterministic policy engine. No LLM, no randomness, no network.

Given an authenticated ``ActionRequest``, a ``TrustedAgentContext`` and a
read-only view of trusted state, it returns ALLOW / BLOCK / ESCALATE with a
stable reason code. The first failing check wins, so decisions are
reproducible and explainable.

The engine's ``SecurityPolicy`` is the single authoritative source of
permissions. It is immutable, and no tool can modify it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from swarmguard.models.actions import (
    ActionRequest,
    AssignTaskParams,
    CompleteTaskParams,
    ModifyEmailParams,
    NoParams,
    PrepareEmailParams,
    RequestApprovalParams,
    SendEmailParams,
    SendMessageParams,
    UpdateSharedDocumentParams,
)
from swarmguard.models.approvals import ApprovalRecord, ApprovalStatus
from swarmguard.models.decisions import Decision, PolicyDecision, ReasonCode
from swarmguard.models.workflows import ResourceType, SharedResource, WorkflowRecord, WorkflowStatus
from swarmguard.security.identity import AgentRole, TrustedAgentContext

EMAIL_FIELDS = ("recipient", "subject", "body")
RESERVED_EMAIL_DOMAINS = frozenset({"example.test", "example.com", "example.org", "example.net"})
RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost")
PROTECTED_MISMATCH_TEXT = "The proposed operation differs from the human-approved operation."


class Access(StrEnum):
    READ = "read"
    WRITE = "write"
    CREATE = "create"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    params_model: type[BaseModel]
    access: Access
    resource_type: ResourceType | None = None
    requires_version: bool = False
    protected: bool = False
    requires_approval: bool = False
    description: str = ""


@dataclass(frozen=True)
class SecurityPolicy:
    version: str
    tools: Mapping[str, ToolSpec]
    role_permissions: Mapping[AgentRole, frozenset[str]]
    approval_required_operations: frozenset[str] = field(default_factory=frozenset)

    def permissions_for(self, role: AgentRole) -> frozenset[str]:
        return self.role_permissions.get(role, frozenset())


def _tools(*specs: ToolSpec) -> Mapping[str, ToolSpec]:
    return MappingProxyType({s.name: s for s in specs})


R = ResourceType
DEFAULT_POLICY = SecurityPolicy(
    version="stage4-v1",
    tools=_tools(
        ToolSpec("read_document", NoParams, Access.READ, R.DOCUMENT),
        ToolSpec("update_shared_document", UpdateSharedDocumentParams, Access.WRITE, R.DOCUMENT, requires_version=True),
        ToolSpec("read_customer_record", NoParams, Access.READ, R.CUSTOMER_RECORD),
        ToolSpec("read_email_draft", NoParams, Access.READ, R.EMAIL_DRAFT),
        ToolSpec("prepare_email", PrepareEmailParams, Access.CREATE),
        ToolSpec("modify_email", ModifyEmailParams, Access.WRITE, R.EMAIL_DRAFT, requires_version=True),
        ToolSpec("send_email", SendEmailParams, Access.READ, R.EMAIL_DRAFT, requires_version=True, protected=True,
                 requires_approval=True, description="Simulated send; requires a bound human approval."),
        ToolSpec("request_approval", RequestApprovalParams, Access.READ, R.EMAIL_DRAFT, requires_version=True,
                 description="Ask the trusted human operator to review an exact resource version."),
        ToolSpec("assign_task", AssignTaskParams, Access.CREATE),
        ToolSpec("complete_task", CompleteTaskParams, Access.WRITE, R.TASK, requires_version=True),
        ToolSpec("send_message", SendMessageParams, Access.CREATE,
                 description="Stage 4: inter-agent message to a workflow participant (content is untrusted data)."),
    ),
    # "Limited" cells of the permission matrix = tool allowed by role, then restricted per resource ACL.
    role_permissions=MappingProxyType({
        AgentRole.COORDINATOR: frozenset({
            "read_document", "update_shared_document", "read_customer_record", "read_email_draft",
            "assign_task", "complete_task", "send_message",
        }),
        AgentRole.RESEARCH: frozenset({
            "read_document", "update_shared_document", "read_customer_record", "complete_task", "send_message",
        }),
        AgentRole.OPERATIONS: frozenset({
            "read_document", "update_shared_document", "read_customer_record", "read_email_draft",
            "prepare_email", "modify_email", "request_approval", "send_email", "complete_task", "send_message",
        }),
    }),
    approval_required_operations=frozenset({"send_email"}),
)


class TrustedStateReader(Protocol):
    """Read-only view of trusted state, supplied by the gateway."""

    def get_workflow(self, workflow_id: str) -> WorkflowRecord | None: ...
    def get_resource(self, resource_id: str) -> SharedResource | None: ...
    def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...
    def get_action(self, action_id: str) -> ActionRequest | None: ...
    def request_id_used(self, agent_id: str, request_id: str, excluding_action_id: str) -> bool: ...
    def is_trusted_approver(self, approver_id: str | None) -> bool: ...
    def verify_approval_integrity(self, record: ApprovalRecord) -> bool: ...
    def compute_operation_hash(self, **kwargs: Any) -> str: ...
    def now(self) -> datetime: ...
    def role_of(self, agent_id: str) -> AgentRole | None: ...
    def get_message_workflow(self, message_id: str) -> str | None: ...


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: PolicyDecision
    params: BaseModel | None = None
    resource: SharedResource | None = None
    approval: ApprovalRecord | None = None
    expected_operation_hash: str | None = None


def is_reserved_email_domain(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return (domain in RESERVED_EMAIL_DOMAINS or any(domain.endswith("." + d) for d in RESERVED_EMAIL_DOMAINS)
            or domain.endswith(RESERVED_TLDS))


def _join(fields: list[str]) -> str:
    return fields[0] if len(fields) == 1 else ", ".join(fields[:-1]) + " and " + fields[-1]


class _Deny(Exception):
    def __init__(self, decision: Decision, code: ReasonCode, explanation: str, **details: Any) -> None:
        super().__init__(explanation)
        self.decision, self.code, self.explanation, self.details = decision, code, explanation, details


B, E = Decision.BLOCK, Decision.ESCALATE


class PolicyEngine:
    def __init__(self, policy: SecurityPolicy = DEFAULT_POLICY) -> None:
        self._policy = policy

    @property
    def policy(self) -> SecurityPolicy:
        return self._policy

    def tool_spec(self, tool: str) -> ToolSpec | None:
        return self._policy.tools.get(tool)

    def permissions_for(self, role: AgentRole) -> frozenset[str]:
        return self._policy.permissions_for(role)

    def evaluate(self, request: ActionRequest, context: TrustedAgentContext, state: TrustedStateReader) -> PolicyEvaluation:
        checks: list[str] = []
        ctx: dict[str, Any] = {}
        try:
            self._run_checks(request, context, state, checks, ctx)
            decision = PolicyDecision(
                action_id=request.action_id, decision=Decision.ALLOW, reason_code=ReasonCode.AUTHORIZED,
                explanation="All deterministic policy checks passed.", checks_passed=checks,
                policy_version=self._policy.version,
            )
        except _Deny as deny:
            decision = PolicyDecision(
                action_id=request.action_id, decision=deny.decision, reason_code=deny.code,
                explanation=deny.explanation, checks_passed=checks, details=dict(deny.details),
                policy_version=self._policy.version,
            )
            ctx.pop("operation_hash", None)
        return PolicyEvaluation(decision, ctx.get("params"), ctx.get("resource"), ctx.get("approval"),
                                ctx.get("operation_hash"))

    # ------------------------------------------------------------------ checks

    def _run_checks(self, request: ActionRequest, context: TrustedAgentContext, state: TrustedStateReader,
                    checks: list[str], ctx: dict[str, Any]) -> None:
        agent = context.agent_id
        if request.agent_id != agent:
            raise _Deny(B, ReasonCode.INVALID_AGENT_IDENTITY, "Request identity does not match the trusted context.")
        if request.claimed_agent_id is not None and request.claimed_agent_id != agent:
            raise _Deny(B, ReasonCode.INVALID_AGENT_IDENTITY,
                        f"Request claims agent_id '{request.claimed_agent_id}' but the authenticated identity "
                        f"is '{agent}'.")
        checks.append("identity")

        spec = self.tool_spec(request.tool)
        if spec is None:
            raise _Deny(B, ReasonCode.UNKNOWN_TOOL, f"Tool '{request.tool}' is not registered.")
        checks.append("tool_registered")

        try:
            params = spec.params_model.model_validate(request.parameters)
        except ValidationError as exc:
            raise _Deny(B, ReasonCode.INVALID_ACTION, f"Parameters for '{spec.name}' failed schema validation.",
                        errors=[f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in exc.errors()]) from None
        if spec.resource_type is None and request.resource_id is not None:
            raise _Deny(B, ReasonCode.INVALID_ACTION, f"'{spec.name}' does not take a resource_id.")
        if spec.resource_type is not None and request.resource_id is None:
            raise _Deny(B, ReasonCode.INVALID_ACTION, f"'{spec.name}' requires a resource_id.")
        if spec.requires_version and request.resource_version is None:
            raise _Deny(B, ReasonCode.INVALID_ACTION,
                        f"'{spec.name}' requires the expected resource_version (no blind overwrites).")
        ctx["params"] = params
        checks.append("schema")

        if request.request_id and state.request_id_used(agent, request.request_id, request.action_id):
            raise _Deny(B, ReasonCode.REPLAYED_REQUEST, f"request_id '{request.request_id}' was already used.")
        checks.append("replay")

        workflow = state.get_workflow(request.workflow_id)
        if workflow is None:
            raise _Deny(B, ReasonCode.WORKFLOW_NOT_FOUND, f"Workflow '{request.workflow_id}' does not exist.")
        if workflow.status is not WorkflowStatus.ACTIVE:
            raise _Deny(B, ReasonCode.WORKFLOW_NOT_ACTIVE, f"Workflow is {workflow.status.value}.")
        if request.workflow_id not in context.authorized_workflows or agent not in workflow.participants:
            raise _Deny(B, ReasonCode.NOT_WORKFLOW_PARTICIPANT, f"'{agent}' is not a member of {request.workflow_id}.")
        checks.append("workflow_membership")

        if spec.name not in context.permissions:
            raise _Deny(B, ReasonCode.AGENT_NOT_AUTHORIZED,
                        f"Role '{context.role.value}' is not permitted to use '{spec.name}'.")
        checks.append("tool_permission")

        for parent_id in request.parent_action_ids:
            parent = state.get_action(parent_id)
            if parent is None:
                raise _Deny(B, ReasonCode.INVALID_ACTION, f"Parent action '{parent_id}' does not exist.")
            if parent.workflow_id != request.workflow_id:
                raise _Deny(B, ReasonCode.WORKFLOW_MISMATCH, f"Parent action '{parent_id}' belongs to another workflow.")
        checks.append("parent_actions")

        resource: SharedResource | None = None
        if spec.resource_type is not None:
            assert request.resource_id is not None
            resource = state.get_resource(request.resource_id)
            if resource is None:
                raise _Deny(B, ReasonCode.RESOURCE_NOT_FOUND, f"Resource '{request.resource_id}' does not exist.")
            if resource.resource_type is not spec.resource_type:
                raise _Deny(B, ReasonCode.INVALID_ACTION,
                            f"'{resource.resource_id}' is a {resource.resource_type.value}, not a {spec.resource_type.value}.")
            if resource.workflow_id is not None and resource.workflow_id != request.workflow_id:
                raise _Deny(B, ReasonCode.WORKFLOW_MISMATCH, f"'{resource.resource_id}' belongs to another workflow.")
            allowed = resource.acl.can_write(agent) if spec.access is Access.WRITE else resource.acl.can_read(agent)
            if not allowed:
                raise _Deny(B, ReasonCode.RESOURCE_ACCESS_DENIED,
                            f"'{agent}' lacks {spec.access.value} access to '{resource.resource_id}'.")
            ctx["resource"] = resource
            checks.append("resource_permission")
            if request.resource_version is not None and request.resource_version != resource.version:
                raise _Deny(B, ReasonCode.RESOURCE_VERSION_MISMATCH,
                            f"'{resource.resource_id}' is at v{resource.version}; request expected "
                            f"v{request.resource_version}.",
                            expected_version=request.resource_version, current_version=resource.version)
            checks.append("resource_version")

        self._tool_rules(spec, params, request, agent, workflow, resource, state)
        checks.append("tool_rules")

        if spec.requires_approval:
            assert isinstance(params, SendEmailParams) and resource is not None
            self._check_approval(spec, params, request, resource, state, ctx)
            checks.append("human_approval")

    def _tool_rules(self, spec: ToolSpec, params: BaseModel, request: ActionRequest, agent: str,
                    workflow: WorkflowRecord, resource: SharedResource | None, state: TrustedStateReader) -> None:
        if isinstance(params, SendMessageParams):
            if params.recipient == agent or params.recipient not in workflow.participants:
                raise _Deny(B, ReasonCode.INVALID_RECIPIENT_AGENT,
                            f"'{params.recipient}' is not another participant of {request.workflow_id}.")
            if params.parent_message_id is not None and \
                    state.get_message_workflow(params.parent_message_id) != request.workflow_id:
                raise _Deny(B, ReasonCode.WORKFLOW_MISMATCH,
                            f"Parent message '{params.parent_message_id}' is not a message of this workflow.")
            return
        recipient = getattr(params, "recipient", None)
        if recipient is not None and not is_reserved_email_domain(recipient):
            raise _Deny(B, ReasonCode.RECIPIENT_NOT_ALLOWED, "Recipients must use reserved example domains.")
        scope = workflow.metadata.get("recipient_scope")
        if recipient is not None and isinstance(scope, list) and recipient not in scope:
            raise _Deny(B, ReasonCode.RECIPIENT_OUT_OF_WORKFLOW_SCOPE,
                        "Recipient is outside this workflow's trusted recipient scope.")
        if isinstance(params, AssignTaskParams):
            assignee_role = state.role_of(params.assignee)
            allowed = self._policy.permissions_for(assignee_role) if assignee_role else frozenset()
            excess = sorted(t for t in params.required_tools if t not in self._policy.tools or t not in allowed)
            if excess:
                raise _Deny(B, ReasonCode.DELEGATION_EXCEEDS_PERMISSIONS,
                            f"A task cannot grant capabilities: {', '.join(excess)} not permitted for "
                            f"'{params.assignee}'.")
            if params.assignee not in workflow.participants:
                raise _Deny(B, ReasonCode.INVALID_ASSIGNEE, f"'{params.assignee}' is not a workflow participant.")
            for task_id in params.depends_on_task_ids:
                task = state.get_resource(task_id)
                if task is None or task.resource_type is not ResourceType.TASK or task.workflow_id != request.workflow_id:
                    raise _Deny(B, ReasonCode.INVALID_ACTION, f"Task dependency '{task_id}' is not a task in this workflow.")
        if isinstance(params, CompleteTaskParams) and resource is not None and resource.content.get("assignee") != agent:
            raise _Deny(B, ReasonCode.TASK_NOT_ASSIGNED, "Only the assignee can complete a task.")
        if isinstance(params, RequestApprovalParams) and params.operation not in self._policy.approval_required_operations:
            raise _Deny(B, ReasonCode.INVALID_ACTION, f"'{params.operation}' is not an approval-gated operation.")
        if isinstance(params, SendEmailParams):
            assert resource is not None
            differing = [k for k in EMAIL_FIELDS if resource.content.get(k) != params.fields()[k]]
            if differing:
                raise _Deny(B, ReasonCode.REQUEST_CONTENT_MISMATCH,
                            f"Requested {_join(differing)} {'does' if len(differing) == 1 else 'do'} not match "
                            f"{resource.resource_id} v{resource.version}.", differing_fields=differing)

    def _check_approval(self, spec: ToolSpec, params: SendEmailParams, request: ActionRequest,
                        resource: SharedResource, state: TrustedStateReader, ctx: dict[str, Any]) -> None:
        if request.approval_id is None:
            raise _Deny(E, ReasonCode.APPROVAL_REQUIRED, f"'{spec.name}' is protected and requires human approval.")
        approval = state.get_approval(request.approval_id)
        if approval is None:
            raise _Deny(B, ReasonCode.APPROVAL_NOT_FOUND,
                        f"Approval '{request.approval_id}' does not exist in the trusted approval store.")
        ctx["approval"] = approval
        if not state.verify_approval_integrity(approval):
            raise _Deny(B, ReasonCode.APPROVAL_INTEGRITY_FAILURE, "Stored approval failed its integrity/signature check.")
        if approval.workflow_id != request.workflow_id:
            raise _Deny(B, ReasonCode.WORKFLOW_MISMATCH, "Approval belongs to a different workflow.")
        if approval.operation != spec.name:
            raise _Deny(B, ReasonCode.APPROVAL_MISMATCH, f"Approval covers '{approval.operation}', not '{spec.name}'.")
        if approval.status is ApprovalStatus.REVOKED:
            raise _Deny(B, ReasonCode.APPROVAL_REVOKED, "A trusted operator revoked this approval.")
        if approval.status is ApprovalStatus.PENDING:
            raise _Deny(E, ReasonCode.APPROVAL_PENDING, "Approval is still awaiting a human decision.")
        if approval.status is ApprovalStatus.REJECTED:
            raise _Deny(B, ReasonCode.APPROVAL_REJECTED, "The human operator rejected this operation.")
        if approval.status is ApprovalStatus.CONSUMED:
            raise _Deny(B, ReasonCode.APPROVAL_ALREADY_USED, "Approval has already been used (single use).")
        if approval.status is ApprovalStatus.EXPIRED or (approval.expires_at is not None and approval.expires_at <= state.now()):
            raise _Deny(B, ReasonCode.APPROVAL_EXPIRED, "Approval has expired.")
        if not state.is_trusted_approver(approval.approver_id):
            raise _Deny(B, ReasonCode.UNTRUSTED_APPROVER, "Approval was not granted by a trusted human operator.")

        requested = {"resource_id": resource.resource_id, "resource_version": resource.version, **params.fields()}
        approved = {"resource_id": approval.resource_id, "resource_version": approval.resource_version,
                    **{k: approval.approved_parameters.get(k) for k in EMAIL_FIELDS}}
        differing = [k for k in ("resource_id", "resource_version", *EMAIL_FIELDS) if approved[k] != requested[k]]
        if differing:
            raise _Deny(B, ReasonCode.APPROVAL_MISMATCH, PROTECTED_MISMATCH_TEXT, differing_fields=differing,
                        approved={k: approved[k] for k in differing}, requested={k: requested[k] for k in differing})
        if approval.status is not ApprovalStatus.APPROVED:
            raise _Deny(B, ReasonCode.APPROVAL_INVALIDATED, approval.status_reason or f"Approval is {approval.status.value}.")
        operation_hash = state.compute_operation_hash(
            approval_id=approval.approval_id, workflow_id=request.workflow_id, operation=spec.name,
            resource_id=resource.resource_id, resource_version=resource.version, parameters=params.fields(),
        )
        if operation_hash != approval.operation_hash:
            raise _Deny(B, ReasonCode.APPROVAL_MISMATCH, PROTECTED_MISMATCH_TEXT, differing_fields=["canonical_binding"])
        ctx["operation_hash"] = operation_hash
