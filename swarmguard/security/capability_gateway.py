"""Capability gateway: the only application path from a proposal to tool execution.

For every submission:

1. resolve the authenticated identity from the opaque credential (received
   separately from the proposal, never read from it),
2. validate the proposal schema; assign a trusted action ID and UTC timestamp,
3. build the ``TrustedAgentContext`` (permissions from the policy engine,
   workflow membership from trusted state),
4. record the proposal,
5. run the deterministic policy engine: workflow membership, tool permission,
   resource permission and version, tool rules, human approval,
6. optionally run the advisory ethics council (it can only escalate),
7. record the decision,
8. on ALLOW only, execute the mock tool with a single-use HMAC permit; the
   tool re-validates protected state inside one SQLite transaction,
9. record the execution result and return a structured response.

Any unexpected error fails closed: BLOCK, nothing executed, decision recorded.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from swarmguard.agents.workspace import SharedWorkspace, WorkspaceError
from swarmguard.clock import Clock
from swarmguard.ethics.council import EthicsCouncil
from swarmguard.models.actions import ID_PATTERN, TOOL_NAME_PATTERN, ActionProposal, ActionRequest
from swarmguard.models.approvals import ApprovalRecord
from swarmguard.models.assessments import CouncilReport
from swarmguard.models.decisions import Decision, DecisionRecord, GatewayResponse, PolicyDecision, ReasonCode
from swarmguard.models.events import ActorType, EventType
from swarmguard.models.workflows import SharedResource, WorkflowRecord
from swarmguard.security.approval_manager import ApprovalError, ApprovalManager
from swarmguard.security.decision_engine import DecisionEngine
from swarmguard.security.identity import AgentCredential, AgentIdentity, IdentityRegistry, TrustedAgentContext
from swarmguard.security.policy_engine import PolicyEngine, PolicyEvaluation
from swarmguard.security.workflow_monitor import GATEWAY_ACTOR, WorkflowMonitor
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories
from swarmguard.tools.mock_tools import MockToolExecutor, PermitError, PreconditionFailed

if TYPE_CHECKING:  # pragma: no cover
    from swarmguard.models.risk import SafetyAssessment
    from swarmguard.monitoring.workflow_context import ObservationBuilder
    from swarmguard.security.workflow_intelligence import WorkflowGuard

logger = logging.getLogger("swarmguard.gateway")

UNAUTHENTICATED = "unauthenticated"
UNKNOWN_WORKFLOW = "unknown_workflow"
# Policy details that are safe to echo back to an untrusted agent (no approved values).
_AGENT_VISIBLE_DETAILS = {"differing_fields", "errors", "expected_version", "current_version"}


class _StateReader:
    """Read-only trusted-state adapter handed to the policy engine."""

    def __init__(self, gateway: CapabilityGateway) -> None:
        self._g = gateway

    def get_workflow(self, workflow_id: str) -> WorkflowRecord | None:
        return self._g._monitor.get_workflow(workflow_id)

    def get_resource(self, resource_id: str) -> SharedResource | None:
        return self._g._workspace.get(resource_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._g._approvals.get(approval_id)

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._g._monitor.get_action(action_id)

    def request_id_used(self, agent_id: str, request_id: str, excluding_action_id: str) -> bool:
        return self._g._repos.actions.request_id_used(agent_id, request_id, excluding_action_id=excluding_action_id)

    def is_trusted_approver(self, approver_id: str | None) -> bool:
        return self._g._approvals.is_trusted_approver(approver_id)

    def verify_approval_integrity(self, record: ApprovalRecord) -> bool:
        return self._g._approvals.verify_integrity(record)

    def compute_operation_hash(self, **kwargs: Any) -> str:
        return ApprovalManager.compute_operation_hash(**kwargs)

    def now(self) -> datetime:
        return self._g._clock.now()

    def role_of(self, agent_id: str):  # noqa: ANN201 - AgentRole | None
        identity = self._g._identities.get(agent_id)
        return identity.role if identity else None

    def get_message_workflow(self, message_id: str) -> str | None:
        message = self._g._repos.messages.get(message_id)
        return message.workflow_id if message else None


class AgentGatewayClient:
    """The only capability an agent receives: submit proposals as itself."""

    __slots__ = ("_gateway", "_credential")

    def __init__(self, gateway: CapabilityGateway, credential: AgentCredential) -> None:
        self._gateway = gateway
        self._credential = credential

    async def submit(self, proposal: ActionProposal | Mapping[str, Any]) -> GatewayResponse:
        return await self._gateway.submit(proposal, self._credential)

    def __repr__(self) -> str:
        return "AgentGatewayClient(<bound>)"


class CapabilityGateway:
    def __init__(
        self,
        *,
        db: Database,
        identities: IdentityRegistry,
        policy_engine: PolicyEngine,
        monitor: WorkflowMonitor,
        workspace: SharedWorkspace,
        approvals: ApprovalManager,
        clock: Clock,
        council: EthicsCouncil | None = None,
        council_tools: frozenset[str] = frozenset({"send_email"}),
        decision_engine: DecisionEngine | None = None,
        workflow_guard: WorkflowGuard | None = None,
        context_builder: ObservationBuilder | None = None,
        max_workflow_proposals: int | None = None,
    ) -> None:
        self._db = db
        self._repos: Repositories = monitor.repos
        self._identities = identities
        self._policy = policy_engine
        self._monitor = monitor
        self._workspace = workspace
        self._approvals = approvals
        self._clock = clock
        self._council = council
        self._council_tools = council_tools
        self._decisions = decision_engine or DecisionEngine()
        self._guard = workflow_guard  # Stage 2 enforcement mode (None = observation mode / Stage 1)
        self._context_builder = context_builder  # Stage 3: as-of observation for the (advisory) council
        self._reader = _StateReader(self)
        if max_workflow_proposals is not None and (type(max_workflow_proposals) is not int or max_workflow_proposals < 1):
            raise ValueError("max_workflow_proposals must be positive")
        self._max_workflow_proposals = max_workflow_proposals
        # The permit key never leaves this object; agents cannot mint permits.
        self._executor = MockToolExecutor(
            permit_key=secrets.token_bytes(32), workspace=workspace, approvals=approvals, monitor=monitor,
            repos=self._repos, db=db, clock=clock,
        )

    def client_for(self, credential: AgentCredential) -> AgentGatewayClient:
        return AgentGatewayClient(self, credential)

    def trusted_context(self, identity: AgentIdentity) -> TrustedAgentContext:
        return TrustedAgentContext(
            identity=identity,
            permissions=self._policy.permissions_for(identity.role),
            authorized_workflows=frozenset(
                w.workflow_id for w in self._monitor.list_workflows() if identity.agent_id in w.participants
            ),
        )

    async def submit(self, proposal: ActionProposal | Mapping[str, Any], credential: object) -> GatewayResponse:
        action_id = self._db.next_id("action")
        raw = proposal.model_dump() if isinstance(proposal, ActionProposal) else proposal
        raw_wf = raw.get("workflow_id") if isinstance(raw, Mapping) else None
        raw_tool = raw.get("tool") if isinstance(raw, Mapping) else None
        workflow_id = raw_wf if isinstance(raw_wf, str) and re.fullmatch(ID_PATTERN, raw_wf) else UNKNOWN_WORKFLOW
        tool = raw_tool if isinstance(raw_tool, str) and re.fullmatch(TOOL_NAME_PATTERN, raw_tool) else "invalid"

        identity = self._identities.resolve(credential)
        if identity is None:
            return self._reject_early(action_id, workflow_id, UNAUTHENTICATED, tool,
                                      ReasonCode.INVALID_AGENT_IDENTITY,
                                      "Request did not carry a valid trusted agent credential.")
        try:
            parsed = proposal if isinstance(proposal, ActionProposal) else ActionProposal.model_validate(proposal)
        except ValidationError as exc:
            return self._reject_early(
                action_id, workflow_id, identity.agent_id, tool, ReasonCode.INVALID_ACTION,
                f"Malformed action proposal ({exc.error_count()} validation error(s)).",
                details={"errors": [f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in exc.errors()]},
            )

        request = ActionRequest(
            action_id=action_id,
            workflow_id=parsed.workflow_id,
            agent_id=identity.agent_id,  # trusted, from the credential; parsed.agent_id is only a claim
            tool=parsed.tool,
            resource_id=parsed.resource_id,
            resource_version=parsed.resource_version,
            parameters=parsed.parameters,
            parent_action_ids=list(parsed.parent_action_ids),
            approval_id=parsed.approval_id,
            request_id=parsed.request_id,
            claimed_agent_id=parsed.agent_id,
            created_at=self._clock.now(),
        )
        self._monitor.record_action(request)

        try:
            return await self._decide_and_execute(request, self.trusted_context(identity))
        except Exception:  # noqa: BLE001 - fail closed
            logger.exception("gateway internal error for %s", action_id)
            return self._finalize(request, PolicyDecision(
                action_id=action_id, decision=Decision.BLOCK, reason_code=ReasonCode.INTERNAL_ERROR,
                explanation="Internal error during authorization; failing closed.",
                policy_version=self._policy.policy.version,
            ), None)

    # ------------------------------------------------------------------ internals

    async def _decide_and_execute(self, request: ActionRequest, context: TrustedAgentContext) -> GatewayResponse:
        evaluation = self._policy.evaluate(request, context, self._reader)
        if self._max_workflow_proposals is not None and len(
                self._monitor.get_workflow_actions(request.workflow_id)) > self._max_workflow_proposals:
            return self._finalize(request, PolicyDecision(
                action_id=request.action_id, decision=Decision.BLOCK,
                reason_code=ReasonCode.EXECUTION_LIMIT_EXCEEDED,
                explanation="Trusted workflow proposal limit exhausted.", policy_version=self._policy.policy.version), None)

        council_report: CouncilReport | None = None
        if self._council is not None and request.tool in self._council_tools:
            council_report = await self._run_council(request, evaluation)

        workflow_risk: SafetyAssessment | None = None
        if (self._guard is not None and request.tool in self._guard.tools
                and evaluation.decision.decision is Decision.ALLOW):
            # Trusted enforcement mode. An exception here propagates and fails closed (BLOCK INTERNAL_ERROR).
            workflow_risk = await self._guard.assess(request)
            self._record_workflow_risk(request, workflow_risk)

        combined = self._decisions.combine(evaluation.decision, council_report, workflow_risk)
        if evaluation.decision.decision is not Decision.ALLOW or combined.decision is not Decision.ALLOW:
            return self._finalize(request, evaluation.decision, council_report, workflow_risk)

        if evaluation.decision.reason_code is not ReasonCode.AUTHORIZED or evaluation.params is None:
            raise RuntimeError("inconsistent ALLOW decision")
        spec = self._policy.tool_spec(request.tool)
        expected: dict[str, Any] = {}
        if evaluation.resource is not None:
            expected["resource_version"] = evaluation.resource.version
        if spec is not None and spec.requires_approval:
            if evaluation.expected_operation_hash is None:
                raise RuntimeError("approval-gated tool allowed without an operation hash")
            expected["operation_hash"] = evaluation.expected_operation_hash

        record = self._record_decision(request, evaluation.decision, council_report, workflow_risk)
        permit = self._executor.issue_permit(request.action_id, request.tool, expected)
        try:
            result = self._executor.execute(request, evaluation.params, permit)
        except (PreconditionFailed, ApprovalError, WorkspaceError, PermitError) as exc:
            return self._execution_failed(request, record, ReasonCode.EXECUTION_PRECONDITION_FAILED, exc)
        except Exception as exc:  # noqa: BLE001 - recorded, nothing half-done (transactions roll back)
            logger.exception("execution error for %s", request.action_id)
            return self._execution_failed(request, record, ReasonCode.EXECUTION_ERROR, exc)

        self._monitor.update_decision_execution(record.decision_id, executed=True, status="COMPLETED")
        self._monitor.record_event(
            EventType.ACTION_EXECUTED, request.workflow_id, agent_id=GATEWAY_ACTOR, action_id=request.action_id,
            tool=request.tool, resource_id=request.resource_id, approval_id=request.approval_id,
            decision=Decision.ALLOW.value, reason_code=ReasonCode.AUTHORIZED.value,
            resource_version_before=request.resource_version,
            resource_version_after=result.get("version", result.get("resource_version")),
            summary=f"Executed {request.tool} for {request.agent_id} ({request.action_id}): {result.get('status')}.",
            details={"result": {k: v for k, v in result.items() if k != "content"}},
        )
        return GatewayResponse(
            action_id=request.action_id, workflow_id=request.workflow_id, tool=request.tool, decision=Decision.ALLOW,
            reason_code=ReasonCode.AUTHORIZED, explanation=record.explanation, executed=True, execution_result=result,
        )

    def _execution_failed(self, request: ActionRequest, record: DecisionRecord, code: ReasonCode,
                          exc: Exception) -> GatewayResponse:
        failed = record.model_copy(update={
            "decision": Decision.BLOCK, "reason_code": code, "explanation": f"Execution aborted: {exc}",
            "executed": False, "execution_status": f"FAILED:{code.value}",
        })
        self._repos.decisions.upsert(failed)
        self._monitor.record_event(
            EventType.ACTION_EXECUTION_FAILED, request.workflow_id, agent_id=GATEWAY_ACTOR,
            action_id=request.action_id, tool=request.tool, resource_id=request.resource_id,
            approval_id=request.approval_id, decision=Decision.BLOCK.value, reason_code=code.value,
            summary=f"Execution of {request.action_id} aborted ({code.value}); transaction rolled back: {exc}",
        )
        return GatewayResponse(
            action_id=request.action_id, workflow_id=request.workflow_id, tool=request.tool, decision=Decision.BLOCK,
            reason_code=code, explanation=f"Execution aborted: {exc}", executed=False,
        )

    async def _run_council(self, request: ActionRequest, evaluation: PolicyEvaluation) -> CouncilReport | None:
        assert self._council is not None
        try:
            context = self._monitor.build_workflow_context(
                request, resource=evaluation.resource, approvals=self._approvals.list_for_workflow(request.workflow_id)
            )
            if self._context_builder is not None:
                snapshot = self._context_builder.snapshot_for_action(request.action_id)
                context = context.model_copy(update={"observation": self._context_builder.global_(
                    snapshot, request.action_id)})
            report = await self._council.deliberate(request, context)
        except Exception:  # noqa: BLE001 - advisory; a crash must not alter the deterministic result
            logger.exception("ethics council failed for %s", request.action_id)
            return None
        for a in report.assessments:
            self._monitor.record_event(
                EventType.ETHICS_ASSESSMENT, request.workflow_id, agent_id=a.monitor_id, actor_type=ActorType.MONITOR,
                action_id=request.action_id, tool=request.tool, resource_id=request.resource_id,
                decision=a.recommendation.value, related_event_ids=a.evidence_event_ids,
                summary=f"[advisory{' MOCK' if a.is_mock else ''}] {a.framework.value}: {a.recommendation.value} "
                f"(risk {a.risk_level.value}). {'; '.join(a.concerns) or 'No concerns.'}",
                details=a.model_dump(mode="json"),
            )
        for f in report.failures:
            self._monitor.record_event(
                EventType.ETHICS_ASSESSMENT, request.workflow_id, agent_id=f.monitor_id, actor_type=ActorType.MONITOR,
                action_id=request.action_id, tool=request.tool, decision="FAILED",
                summary=f"[advisory] {f.framework.value} monitor FAILED: {f.error_type}: {f.message}",
                details=f.model_dump(mode="json"),
            )
        return report

    def _record_workflow_risk(self, request: ActionRequest, a: SafetyAssessment) -> None:
        self._monitor.record_event(
            EventType.WORKFLOW_RISK_ASSESSMENT, request.workflow_id, agent_id=f"swarmguard.guard.{a.monitor_id}",
            actor_type=ActorType.MONITOR, action_id=request.action_id, tool=request.tool,
            resource_id=request.resource_id, decision=a.recommendation.value, related_event_ids=a.evidence_event_ids,
            summary=f"[workflow guard {a.monitor_id}] {a.recommendation.value} (risk {a.risk_level.value}, "
            f"uncertainty {a.uncertainty.value}); patterns: {', '.join(p.value for p in a.detected_patterns) or 'none'}.",
            details={"assessment": a.model_dump(mode="json", exclude={"findings", "data_flows"})},
        )

    def _record_decision(self, request: ActionRequest, policy: PolicyDecision,
                         council: CouncilReport | None, workflow_risk: SafetyAssessment | None = None) -> DecisionRecord:
        combined = self._decisions.combine(policy, council, workflow_risk)
        details = dict(policy.details)
        if workflow_risk is not None:
            details["workflow_guard"] = {"monitor_id": workflow_risk.monitor_id,
                                         "recommendation": workflow_risk.recommendation.value,
                                         "patterns": [p.value for p in workflow_risk.detected_patterns]}
        record = DecisionRecord(
            decision_id=self._db.next_id("decision"), action_id=request.action_id, workflow_id=request.workflow_id,
            agent_id=request.agent_id, tool=request.tool, decision=combined.decision,
            reason_code=combined.reason_code, explanation=combined.explanation, details=details,
            policy_decision=policy.decision, policy_reason_code=policy.reason_code,
            checks_passed=policy.checks_passed, advisory_recommendation=combined.advisory_recommendation,
            advisory_summary=combined.advisory_summary, policy_version=policy.policy_version,
            decided_at=self._clock.now(),
        )
        self._monitor.record_decision(record)
        return record

    def _finalize(self, request: ActionRequest, policy: PolicyDecision, council: CouncilReport | None,
                  workflow_risk: SafetyAssessment | None = None) -> GatewayResponse:
        record = self._record_decision(request, policy, council, workflow_risk)
        spec = self._policy.tool_spec(request.tool)
        if record.decision is Decision.BLOCK and spec is not None and spec.protected:
            self._monitor.record_event(
                EventType.SECURITY_ALERT, request.workflow_id, agent_id=GATEWAY_ACTOR, action_id=request.action_id,
                tool=request.tool, resource_id=request.resource_id, approval_id=request.approval_id,
                decision=record.decision.value, reason_code=record.reason_code.value,
                summary=f"Protected operation {request.tool} by {request.agent_id} blocked "
                f"[{record.reason_code.value}]. Execution prevented.",
                details=policy.details,
            )
        return GatewayResponse(
            action_id=request.action_id, workflow_id=request.workflow_id, tool=request.tool,
            decision=record.decision, reason_code=record.reason_code, explanation=record.explanation,
            details={k: v for k, v in policy.details.items() if k in _AGENT_VISIBLE_DETAILS}, executed=False,
        )

    def _reject_early(self, action_id: str, workflow_id: str, agent_id: str, tool: str, code: ReasonCode,
                      explanation: str, details: dict[str, Any] | None = None) -> GatewayResponse:
        """Persist a BLOCK for submissions that never became a valid ActionRequest."""
        record = DecisionRecord(
            decision_id=self._db.next_id("decision"), action_id=action_id, workflow_id=workflow_id, agent_id=agent_id,
            tool=tool, decision=Decision.BLOCK, reason_code=code, explanation=explanation, details=details or {},
            policy_decision=Decision.BLOCK, policy_reason_code=code, policy_version=self._policy.policy.version,
            decided_at=self._clock.now(),
        )
        self._monitor.record_decision(record)
        self._monitor.record_event(
            EventType.SECURITY_ALERT, workflow_id, agent_id=GATEWAY_ACTOR, action_id=action_id,
            decision=Decision.BLOCK.value, reason_code=code.value,
            summary=f"Rejected submission {action_id} from {agent_id}: {explanation}",
        )
        return GatewayResponse(
            action_id=action_id, workflow_id=workflow_id, tool=tool, decision=Decision.BLOCK, reason_code=code,
            explanation=explanation, details={k: v for k, v in (details or {}).items() if k in _AGENT_VISIBLE_DETAILS},
            executed=False,
        )
