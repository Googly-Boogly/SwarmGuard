"""Capability gateway: execution, fail-closed behaviour, bypass resistance (spec items 13, 22)."""

from __future__ import annotations

import pytest

from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.models.events import EventType
from swarmguard.tools import mock_tools
from swarmguard.tools.mock_tools import ExecutionPermit, PermitError
from tests.conftest import approve, make_email, modify, run, send, sent_count


def test_13_valid_actions_execute(sim, workflow):
    email_id = make_email(sim, workflow)
    assert modify(sim, workflow, email_id, 1, body="Updated body.").executed
    approval_id = approve(sim, workflow, email_id, version=2)
    r = send(sim, workflow, email_id, 2, approval_id, body="Updated body.")
    assert (r.decision, r.reason_code, r.executed) == (Decision.ALLOW, ReasonCode.AUTHORIZED, True)
    assert r.execution_result["status"] == "SIMULATED_EMAIL_SENT" and r.execution_result["real_delivery"] is False
    assert sent_count(sim) == 1


def test_response_shape(sim, workflow):
    email_id = make_email(sim, workflow)
    blocked = send(sim, workflow, email_id, 1, "approval_404")
    body = blocked.model_dump(mode="json")
    assert {k: body[k] for k in ("decision", "reason_code", "executed", "execution_result")} == {
        "decision": "BLOCK", "reason_code": "APPROVAL_NOT_FOUND", "executed": False, "execution_result": None}


def test_22_mandatory_validation_failures_prevent_execution(sim, workflow, monkeypatch):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)

    def boom(*a, **k):
        raise RuntimeError("policy backend exploded")

    with monkeypatch.context() as m:
        m.setattr(sim.policy_engine, "evaluate", boom)
        r = send(sim, workflow, email_id, 1, approval_id)
    assert (r.decision, r.reason_code, r.executed) == (Decision.BLOCK, ReasonCode.INTERNAL_ERROR, False)
    assert sim.monitor.get_decision_for_action(r.action_id).reason_code is ReasonCode.INTERNAL_ERROR

    # Request content that does not match the stored draft is refused before any approval check.
    r = send(sim, workflow, email_id, 1, approval_id, recipient="other@example.test")
    assert r.reason_code is ReasonCode.REQUEST_CONTENT_MISMATCH
    assert sent_count(sim) == 0
    assert sim.approvals.get(approval_id).status.value == "APPROVED"


def test_execution_rechecks_state_and_rolls_back(sim, workflow, monkeypatch):
    """Simulate the draft changing between validation and execution (TOCTOU)."""
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    real_execute = sim.gateway._executor.execute

    def racing_execute(request, params, permit):
        sim.db.execute("UPDATE resources SET version = version + 1 WHERE resource_id = ?", (email_id,))
        return real_execute(request, params, permit)

    monkeypatch.setattr(sim.gateway._executor, "execute", racing_execute)
    r = send(sim, workflow, email_id, 1, approval_id)
    assert (r.reason_code, r.executed) == (ReasonCode.EXECUTION_PRECONDITION_FAILED, False)
    assert sent_count(sim) == 0
    assert sim.approvals.get(approval_id).status.value == "APPROVED"  # consumption rolled back
    record = sim.monitor.get_decision_for_action(r.action_id)
    assert record.decision is Decision.BLOCK and record.execution_status.startswith("FAILED")


def test_unauthorized_send_attempts_create_no_send_records(sim, workflow):
    email_id = make_email(sim, workflow)
    approval_id = approve(sim, workflow, email_id)
    attempts = [
        run(sim.research.propose(workflow, "send_email", {"recipient": "customer@example.test",
                                                          "subject": "Service Update",
                                                          "body": "Your service request has been received."},
                                 resource_id=email_id, resource_version=1, approval_id=approval_id)),
        send(sim, workflow, email_id, 1, None),
        send(sim, workflow, email_id, 1, "approval_999"),
        send(sim, workflow, email_id, 1, approval_id, subject="Different"),
    ]
    assert all(not a.executed for a in attempts)
    assert sent_count(sim) == 0
    assert not sim.monitor.find_events(event_type=EventType.EMAIL_SENT_SIMULATED)
    alerts = sim.monitor.find_events(event_type=EventType.SECURITY_ALERT, workflow_id=workflow)
    assert len(alerts) >= 2


def test_replayed_request_ids_rejected(sim, workflow):
    p = {"recipient": "a@example.test", "subject": "s", "body": "b"}
    first = run(sim.operations.propose(workflow, "prepare_email", p, request_id="req-1"))
    replay = run(sim.operations.propose(workflow, "prepare_email", p, request_id="req-1"))
    assert first.executed and (replay.decision, replay.reason_code) == (Decision.BLOCK, ReasonCode.REPLAYED_REQUEST)


def test_malformed_proposals_rejected_and_recorded(sim, workflow):
    for raw in ({"tool": "read_document"}, {"workflow_id": workflow, "tool": "Bad Tool!"},
                {"workflow_id": workflow, "tool": "read_document", "parameters": {"x": "y" * 20_000}},
                {"workflow_id": workflow, "tool": "read_document", "resource_version": "1"},
                "not a dict", None):
        r = run(sim.operations.submit_raw(raw))
        assert r.decision is Decision.BLOCK and r.reason_code is ReasonCode.INVALID_ACTION
        assert sim.monitor.repos.decisions.for_action(r.action_id) is not None


def test_tools_cannot_be_invoked_outside_the_gateway(sim, workflow):
    email_id = make_email(sim, workflow)
    request = sim.monitor.get_action(sim.operations.responses[-1].action_id)
    executor = sim.gateway._executor
    for permit in (ExecutionPermit(request.action_id, request.tool, {}, mac="0" * 64),
                   ExecutionPermit(request.action_id, request.tool, {})):
        with pytest.raises(PermitError):
            executor.execute(request, None, permit)  # type: ignore[arg-type]
    # A hand-built ToolContext with a forged verifier still cannot reach a real executor's key.
    with pytest.raises(PermitError):
        ctx = mock_tools.ToolContext(request=request, permit=ExecutionPermit(request.action_id, request.tool),
                                     workspace=sim.workspace, approvals=sim.approvals, monitor=sim.monitor,
                                     repos=sim.repos, db=sim.db, clock=sim.clock, _verifier=executor._verify)
        mock_tools.send_email(ctx, None)  # type: ignore[arg-type]
    # Permits are single use.
    good = executor.issue_permit("action_x", "read_document", {})
    from swarmguard.models.actions import ActionRequest, NoParams
    req = ActionRequest(action_id="action_x", workflow_id=workflow, agent_id="research_agent", tool="read_document",
                        resource_id="doc_service_faq", created_at=sim.clock.now())
    executor.execute(req, NoParams(), good)
    with pytest.raises(PermitError):
        executor.execute(req, NoParams(), good)
    assert sent_count(sim) == 0 and email_id
