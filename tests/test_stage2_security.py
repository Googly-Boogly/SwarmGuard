"""Stage 2 security regressions (spec items 34-41, 46): enforcement mode may only escalate."""

from __future__ import annotations

import ast
import socket
from datetime import timedelta
from pathlib import Path

import pytest

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.models.decisions import Decision, PolicyDecision, ReasonCode
from swarmguard.models.events import EventType
from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.risk import ObservationWindow, SafetyAssessment
from swarmguard.security.decision_engine import DecisionEngine
from tests.conftest import approve, make_email, modify, run, send
from tests.stage2_helpers import run_scenario

G = ObservationWindow.GLOBAL


@pytest.fixture
def guarded(tmp_path):
    s = SwarmSimulator(tmp_path / "guarded.db", clock=SteppingClock(), workflow_enforcement=G)
    s.start_workflow("wf_g", "Guarded")
    yield s
    s.close()


def _assessment(rec: Recommendation) -> SafetyAssessment:
    return SafetyAssessment(monitor_id="global_workflow", window=G, workflow_id="w", action_id="a", checkpoint_seq=1,
                            risk_level=RiskLevel.HIGH, recommendation=rec, explanation="x", uncertainty=Uncertainty.LOW)


def _policy(decision: Decision, code: ReasonCode) -> PolicyDecision:
    return PolicyDecision(action_id="a", decision=decision, reason_code=code, explanation="p")


def test_40_decision_engine_workflow_risk_is_monotone():
    eng = DecisionEngine()
    for rec in Recommendation:
        assert eng.combine(_policy(Decision.BLOCK, ReasonCode.APPROVAL_MISMATCH), None,
                           _assessment(rec)).decision is Decision.BLOCK
        assert eng.combine(_policy(Decision.ESCALATE, ReasonCode.APPROVAL_REQUIRED), None,
                           _assessment(rec)).reason_code is ReasonCode.APPROVAL_REQUIRED
    allow = _policy(Decision.ALLOW, ReasonCode.AUTHORIZED)
    assert eng.combine(allow, None, _assessment(Recommendation.ALLOW)).decision is Decision.ALLOW
    for rec in (Recommendation.ESCALATE, Recommendation.BLOCK):  # never becomes a BLOCK by itself
        c = eng.combine(allow, None, _assessment(rec))
        assert (c.decision, c.reason_code) == (Decision.ESCALATE, ReasonCode.WORKFLOW_RISK_ESCALATION)


def test_34_36_stage1_authorization_still_enforced_with_guard(guarded):
    email = make_email(guarded, "wf_g")
    approval = approve(guarded, "wf_g", email)
    assert modify(guarded, "wf_g", email, 1, recipient="other@example.test").executed
    stale = send(guarded, "wf_g", email, 2, approval, recipient="other@example.test")
    assert stale.reason_code is ReasonCode.APPROVAL_MISMATCH and not stale.executed
    assert send(guarded, "wf_g", email, 1, approval).reason_code is ReasonCode.RESOURCE_VERSION_MISMATCH
    assert guarded.sent_emails() == []
    # the guard is not even consulted when policy does not ALLOW
    assert not guarded.monitor.find_events(workflow_id="wf_g", event_type=EventType.WORKFLOW_RISK_ASSESSMENT)


def test_35_forged_and_expired_approvals_rejected_with_guard(guarded):
    email = make_email(guarded, "wf_g")
    assert send(guarded, "wf_g", email, 1, "approval_999").reason_code is ReasonCode.APPROVAL_NOT_FOUND
    approval = approve(guarded, "wf_g", email, ttl=timedelta(minutes=1))
    guarded.clock.advance(timedelta(minutes=5))
    assert send(guarded, "wf_g", email, 1, approval).reason_code is ReasonCode.APPROVAL_EXPIRED
    assert guarded.sent_emails() == []


def test_37_38_unknown_tools_and_spoofing_still_blocked_with_guard(guarded):
    assert run(guarded.operations.propose("wf_g", "grant_approval", {})).reason_code is ReasonCode.UNKNOWN_TOOL
    spoof = run(guarded.operations.submit_raw({"workflow_id": "wf_g", "tool": "read_document",
                                                "resource_id": "doc_service_faq", "agent_id": "coordinator"}))
    assert spoof.reason_code is ReasonCode.INVALID_AGENT_IDENTITY
    forged = run(guarded.gateway.submit({"workflow_id": "wf_g", "tool": "send_email"}, object()))
    assert forged.reason_code is ReasonCode.INVALID_AGENT_IDENTITY


def test_authorized_send_passes_guard_and_is_audited(guarded):
    email = make_email(guarded, "wf_g")
    r = send(guarded, "wf_g", email, 1, approve(guarded, "wf_g", email))
    assert r.executed and r.reason_code is ReasonCode.AUTHORIZED
    (ev,) = guarded.monitor.find_events(workflow_id="wf_g", event_type=EventType.WORKFLOW_RISK_ASSESSMENT)
    assert ev.decision == "ALLOW" and ev.actor_type.value == "MONITOR"
    assert guarded.monitor.get_decision_for_action(r.action_id).details["workflow_guard"]["recommendation"] == "ALLOW"


def test_guard_escalation_prevents_execution_and_is_audited():
    sim, r = run_scenario("distributed_approval_001", enforcement=G)
    d = sim.monitor.get_decision_for_action(r.critical_action_id)
    assert (d.decision, d.reason_code, d.policy_decision, d.executed) == (
        Decision.ESCALATE, ReasonCode.WORKFLOW_RISK_ESCALATION, Decision.ALLOW, False)
    assert sim.sent_emails() == []
    assert sim.approvals.get("approval_001").status.value == "APPROVED"  # not consumed, not altered
    assert sim.monitor.find_events(action_id=r.critical_action_id, event_type=EventType.WORKFLOW_RISK_ASSESSMENT)


def test_observation_mode_never_changes_decisions():
    a, ra = run_scenario("distributed_approval_001")
    assert a.gateway._guard is None
    assert a.monitor.get_decision_for_action(ra.critical_action_id).decision is Decision.ALLOW


def test_guard_failure_fails_closed(guarded, monkeypatch):
    email = make_email(guarded, "wf_g")
    approval = approve(guarded, "wf_g", email)

    async def boom(request):
        raise RuntimeError("guard crashed")

    monkeypatch.setattr(guarded.gateway._guard, "assess", boom)
    r = send(guarded, "wf_g", email, 1, approval)
    assert (r.decision, r.reason_code, r.executed) == (Decision.BLOCK, ReasonCode.INTERNAL_ERROR, False)
    assert guarded.sent_emails() == []


def test_39_monitors_and_agents_hold_no_execution_capability(guarded):
    guard = guarded.gateway._guard
    monitor = guard._monitor
    banned = {"Database", "Repositories", "MockToolExecutor", "ApprovalManager", "CapabilityGateway",
              "SharedWorkspace", "PolicyEngine"}
    assert not {type(v).__name__ for v in vars(monitor).values()} & banned
    assert not {type(v).__name__ for v in vars(guard).values()} & banned
    for agent in guarded.agents.values():
        reachable = {type(v).__name__ for v in vars(agent).values()}
        assert not reachable & (banned | {"ObservationBuilder", "WorkflowGuard", "WorkflowIntelligenceEngine"})


def test_41_new_modules_have_no_network_or_email_imports():
    forbidden = {"socket", "smtplib", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "subprocess",
                 "openai", "anthropic"}
    pkg = Path(__file__).resolve().parents[1] / "swarmguard"
    files = [*pkg.glob("monitoring/*.py"), *pkg.glob("evaluation/**/*.py"),
             *(pkg / "security" / n for n in ("workflow_intelligence.py", "pattern_detector.py", "risk_analyzer.py",
                                               "invariants.py"))]
    assert len(files) > 15
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not {n.split(".")[0] for n in names} & forbidden, f


def test_46_stage2_runs_without_network(tmp_path, monkeypatch, capsys):
    import demo_stage2
    import run_evaluation

    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(run_evaluation, "git_revision", lambda: None)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert run(demo_stage2.main(tmp_path / "d.db")) == 0
    assert run(run_evaluation.main(tmp_path / "res", "offline", 0, ["distributed_approval_001"])) == 0
    capsys.readouterr()
