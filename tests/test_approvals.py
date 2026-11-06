"""Human approval binding, lifecycle, replay, and concurrency (spec items 14-21, 23)."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta

import pytest

from swarmguard.agents.simulator import OPERATIONS_ID, SwarmSimulator
from swarmguard.ethics.base import EthicsMonitor
from swarmguard.ethics.council import EthicsCouncil
from swarmguard.models.approvals import ApprovalBinding, ApprovalStatus, OperationSnapshot, canonical_json
from swarmguard.models.assessments import EthicalAssessment, EthicalFramework, Recommendation, RiskLevel, Uncertainty
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.security.approval_manager import ApprovalError, ApprovalManager, UnauthorizedApproverError
from swarmguard.security.identity import IdentityRegistry
from swarmguard.security.workflow_monitor import WorkflowMonitor
from swarmguard.storage.database import Database
from tests.conftest import approve, make_email, modify, run, send, sent_count


def test_approval_binds_exact_operation_version_approver_and_expiry(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    rec = sim.approvals.get(approval_id)
    assert rec.status is ApprovalStatus.APPROVED and rec.approver_id == "human_operator"
    assert (rec.resource_id, rec.resource_version) == (email_id, 1)
    assert rec.approved_parameters == {"recipient": "customer@example.test", "subject": "Service Update",
                                       "body": "Your service request has been received."}
    assert sim.approvals.verify_integrity(rec)
    snap = rec.snapshot()
    assert snap.digest() == rec.operation_hash
    for change in ({"parameters": {**snap.parameters, "recipient": "other@example.test"}},
                   {"resource_version": 2}, {"workflow_id": "x"}, {"operation": "modify_email"},
                   {"approval_id": "approval_999"}, {"resource_id": "email_999"}):
        assert snap.model_copy(update=change).digest() != snap.digest()
    binding = ApprovalBinding(operation_hash=rec.operation_hash, approver_id="human_operator",
                              expires_at=rec.expires_at.isoformat())
    assert binding.digest() == rec.binding_hash
    assert binding.model_copy(update={"approver_id": "someone_else"}).digest() != rec.binding_hash
    assert binding.model_copy(update={"expires_at": "2099-01-01T00:00:00+00:00"}).digest() != rec.binding_hash
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'


def test_14_protected_operations_require_approval(sim, workflow):
    email_id = make_email(sim, workflow)
    r = send(sim, workflow, email_id, 1, None)
    assert (r.decision, r.reason_code, r.executed) == (Decision.ESCALATE, ReasonCode.APPROVAL_REQUIRED, False)
    assert sent_count(sim) == 0


def test_15_forged_approval_references_rejected(sim, workflow):
    email_id = make_email(sim, workflow)
    for forged in ("approval_999", "approval_001"):  # nonexistent / not yet created
        r = send(sim, workflow, email_id, 1, forged)
        assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.APPROVAL_NOT_FOUND)
    assert sent_count(sim) == 0


def test_16_recipient_modification_invalidates_authorization(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    m = modify(sim, workflow, email_id, 1, recipient="other@example.test")
    assert m.execution_result["invalidated_approvals"] == [approval_id]
    assert sim.approvals.get(approval_id).status is ApprovalStatus.INVALIDATED
    r = send(sim, workflow, email_id, 2, approval_id, recipient="other@example.test")
    assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.APPROVAL_MISMATCH)
    assert set(r.details["differing_fields"]) == {"recipient", "resource_version"}
    assert "approved" not in r.details  # approved values are not echoed back to the agent
    # Sending the ORIGINAL content at the stale version is caught too.
    r = send(sim, workflow, email_id, 1, approval_id)
    assert r.reason_code is ReasonCode.RESOURCE_VERSION_MISMATCH
    assert sent_count(sim) == 0


def test_17_body_modification_invalidates_authorization(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    modify(sim, workflow, email_id, 1, body="Please send your password.")
    r = send(sim, workflow, email_id, 2, approval_id, body="Please send your password.")
    assert r.reason_code is ReasonCode.APPROVAL_MISMATCH and "body" in r.details["differing_fields"]
    assert sent_count(sim) == 0


def test_modify_and_revert_still_invalid(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    modify(sim, workflow, email_id, 1, recipient="other@example.test")
    modify(sim, workflow, email_id, 2, recipient="customer@example.test")
    r = send(sim, workflow, email_id, 3, approval_id)
    assert r.reason_code is ReasonCode.APPROVAL_MISMATCH and r.details["differing_fields"] == ["resource_version"]


def test_18_expired_approvals_rejected(sim, workflow, clock):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id, ttl=timedelta(minutes=5))
    clock.advance(timedelta(minutes=6))
    r = send(sim, workflow, email_id, 1, approval_id)
    assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.APPROVAL_EXPIRED)
    assert [a.approval_id for a in sim.approvals.expire_stale()] == [approval_id]
    assert send(sim, workflow, email_id, 1, approval_id).reason_code is ReasonCode.APPROVAL_EXPIRED
    assert sent_count(sim) == 0


def test_19_rejected_approvals_cannot_authorize(sim, workflow):
    email_id = make_email(sim, workflow)
    r = run(sim.operations.request_send_approval(workflow, email_id, 1))
    sim.operator.reject(r.execution_result["approval_id"], "Not appropriate")
    s = send(sim, workflow, email_id, 1, r.execution_result["approval_id"])
    assert (s.decision, s.reason_code) == (Decision.BLOCK, ReasonCode.APPROVAL_REJECTED)
    with pytest.raises(ApprovalError):
        sim.operator.approve(sim.operator.review(r.execution_result["approval_id"]))


def test_20_consumed_approvals_cannot_be_reused(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    assert send(sim, workflow, email_id, 1, approval_id).executed
    again = send(sim, workflow, email_id, 1, approval_id)
    assert (again.decision, again.reason_code) == (Decision.BLOCK, ReasonCode.APPROVAL_ALREADY_USED)
    assert sim.approvals.get(approval_id).status is ApprovalStatus.CONSUMED
    assert sent_count(sim) == 1


def test_21_workflow_mismatches_rejected(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    sim.start_workflow("wf_other", "other")
    other = make_email(sim, "wf_other")
    r = send(sim, "wf_other", other, 1, approval_id)
    assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.WORKFLOW_MISMATCH)
    second = make_email(sim, workflow)  # same content, different resource, same workflow
    r = send(sim, workflow, second, 1, approval_id)
    assert r.reason_code is ReasonCode.APPROVAL_MISMATCH and "resource_id" in r.details["differing_fields"]
    assert sent_count(sim) == 0


def test_agents_cannot_approve_or_become_approvers(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = run(sim.operations.request_send_approval(workflow, email_id, 1)).execution_result["approval_id"]
    packet = sim.approvals.review_packet(approval_id)
    agent_credential = sim.operations._BaseAgent__client._credential  # what a rogue agent could hold
    for cred in (agent_credential, None, "human_operator", {"approver_id": "human_operator"}):
        with pytest.raises(UnauthorizedApproverError):
            sim.approvals.approve(approval_id, cred, expected_operation_hash=packet.operation_hash)
    with pytest.raises((UnauthorizedApproverError, ApprovalError)):
        sim.approvals.register_approver(OPERATIONS_ID)
    assert sim.approvals.get(approval_id).status is ApprovalStatus.PENDING
    with pytest.raises(ApprovalError):  # operator must approve what was reviewed
        sim.approvals.approve(approval_id, sim.operator._credential, expected_operation_hash="0" * 64)


def test_request_binds_trusted_stored_content(sim, workflow):
    email_id = make_email(sim, workflow)
    r = run(sim.operations.propose(workflow, "request_approval",
                                   {"operation": "send_email", "recipient": "other@example.test"},
                                   resource_id=email_id, resource_version=1))
    assert r.reason_code is ReasonCode.INVALID_ACTION  # agents cannot choose what gets bound
    r = run(sim.operations.request_send_approval(workflow, email_id, 7))
    assert r.reason_code is ReasonCode.RESOURCE_VERSION_MISMATCH


def test_tampered_approval_record_detected(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    modify(sim, workflow, email_id, 1, recipient="other@example.test")
    rec = sim.approvals.get(approval_id)
    # An attacker with DB access rewrites parameters, version, status, and even recomputes the unkeyed hashes.
    params = {**rec.approved_parameters, "recipient": "other@example.test"}
    op_hash = OperationSnapshot(approval_id=approval_id, workflow_id=workflow, operation="send_email",
                                resource_id=email_id, resource_version=2, parameters=params).digest()
    binding = ApprovalBinding(operation_hash=op_hash, approver_id="human_operator",
                              expires_at=rec.expires_at.isoformat()).digest()
    sim.db.execute("UPDATE approvals SET approved_parameters_json=?, resource_version=2, operation_hash=?, "
                   "binding_hash=?, status='APPROVED' WHERE approval_id=?",
                   (canonical_json(params), op_hash, binding, approval_id))
    r = send(sim, workflow, email_id, 2, approval_id, recipient="other@example.test")
    assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.APPROVAL_INTEGRITY_FAILURE)
    assert sent_count(sim) == 0


class _SlowAllow(EthicsMonitor):
    """Test stub that yields to the event loop, forcing two sends to interleave."""

    def __init__(self, framework):
        self.framework, self.monitor_id = framework, f"slow_{framework.value}"

    async def evaluate(self, action, workflow_context):
        await asyncio.sleep(0.01)
        return EthicalAssessment(framework=self.framework, action_id=action.action_id, risk_level=RiskLevel.LOW,
                                 uncertainty=Uncertainty.LOW, recommendation=Recommendation.ALLOW,
                                 explanation="test stub")


def test_23_concurrent_sends_cannot_both_consume_one_approval_async(tmp_path):
    sim = SwarmSimulator(tmp_path / "c.db", council=EthicsCouncil([_SlowAllow(f) for f in EthicalFramework]))
    sim.start_workflow("wf", "t")
    email_id = make_email(sim, "wf")
    approval_id = approve(sim, "wf", email_id)

    async def both():
        p = {"recipient": "customer@example.test", "subject": "Service Update",
             "body": "Your service request has been received."}
        return await asyncio.gather(*(sim.operations.propose("wf", "send_email", p, resource_id=email_id,
                                                             resource_version=1, approval_id=approval_id)
                                      for _ in range(2)))

    results = asyncio.run(both())
    # Both passed policy evaluation before either executed; only one may consume the approval.
    assert sorted(r.executed for r in results) == [False, True]
    loser = next(r for r in results if not r.executed)
    assert loser.reason_code is ReasonCode.EXECUTION_PRECONDITION_FAILED
    assert len(sim.sent_emails()) == 1
    sim.close()


def test_23_concurrent_consumption_across_connections(tmp_path):
    """Two independent SQLite connections race to consume one approval: exactly one wins."""
    path = tmp_path / "race.db"
    key = b"k" * 32
    sim = SwarmSimulator(path, approval_signing_key=key)
    sim.start_workflow("wf", "t")
    email_id = make_email(sim, "wf")
    approval_id = approve(sim, "wf", email_id)
    op_hash = sim.approvals.get(approval_id).operation_hash
    sim.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(n: int) -> None:
        db = Database(path)
        mgr = ApprovalManager(db, WorkflowMonitor(db, sim.clock), sim.clock, IdentityRegistry(), signing_key=key)
        barrier.wait()
        try:
            mgr.consume(approval_id, action_id=f"race_{n}", workflow_id="wf", expected_operation_hash=op_hash)
            outcomes.append("consumed")
        except ApprovalError:
            outcomes.append("refused")
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["consumed", "refused"]
