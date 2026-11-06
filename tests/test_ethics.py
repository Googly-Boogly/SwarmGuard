"""Ethics schemas, mock monitors, and council aggregation (spec items 31-36)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.ethics.base import EthicsMonitor
from swarmguard.ethics.care import MockCareEthicsMonitor
from swarmguard.ethics.consequentialism import MockConsequentialistMonitor
from swarmguard.ethics.council import EthicsCouncil, build_mock_council
from swarmguard.ethics.deontology import MockDeontologicalMonitor
from swarmguard.ethics.virtue import MockVirtueEthicsMonitor
from swarmguard.evaluation.baselines import ContextScope, restrict_context
from swarmguard.evaluation.scenarios import run_approval_laundering, run_authorized_email
from swarmguard.models.assessments import (
    EthicalAssessment,
    EthicalFramework,
    Recommendation,
    RiskLevel,
    Uncertainty,
)
from swarmguard.models.decisions import Decision, ReasonCode
from tests.conftest import approve, make_email, modify, run, send

F = EthicalFramework
VALID = {"framework": "virtue_ethics", "action_id": "action_001", "risk_level": "HIGH",
         "concerns": ["Potential dishonesty"], "evidence_event_ids": ["event_014"], "uncertainty": "MEDIUM",
         "recommendation": "ESCALATE", "explanation": "The proposed operation differs from prior authorization."}
MOCKS = (MockVirtueEthicsMonitor, MockConsequentialistMonitor, MockDeontologicalMonitor, MockCareEthicsMonitor)


def _context(sim, scenario=run_approval_laundering, wf="wf"):
    o = run(scenario(sim, wf))
    action = sim.monitor.get_action(o.final.action_id)
    return action, sim.monitor.build_workflow_context(action, resource=sim.workspace.get(o.email_id),
                                                      approvals=sim.approvals.list_for_workflow(wf))


def test_31_assessment_validates_structured_output():
    a = EthicalAssessment.model_validate(VALID)
    assert a.framework is F.VIRTUE_ETHICS and a.evidence_event_ids == ["event_014"]
    assert EthicalAssessment.model_validate_json(a.model_dump_json()) == a
    assert {r.value for r in RiskLevel} == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert {u.value for u in Uncertainty} == {"LOW", "MEDIUM", "HIGH"}
    with pytest.raises(ValidationError):
        EthicalAssessment.model_validate({k: v for k, v in VALID.items() if k != "uncertainty"})


@pytest.mark.parametrize("bad", [{"recommendation": "MAYBE"}, {"recommendation": "allow!"}, {"risk_level": "NONE"},
                                 {"framework": "utilitarian_vibes"}, {"uncertainty": "UNKNOWN"}, {"explanation": ""},
                                 {"unexpected": 1}])
def test_32_invalid_values_rejected(bad):
    with pytest.raises(ValidationError):
        EthicalAssessment.model_validate({**VALID, **bad})


@pytest.mark.parametrize("scenario", [run_approval_laundering, run_authorized_email])
def test_33_all_four_mock_monitors_return_valid_labeled_assessments(sim, scenario):
    action, ctx = _context(sim, scenario)
    for cls in MOCKS:
        a = run(cls().evaluate(action, ctx))
        assert isinstance(a, EthicalAssessment) and a.framework is cls.framework and a.action_id == action.action_id
        assert a.is_mock and a.explanation.startswith("[MOCK heuristic, not an LLM assessment]")
        assert all(sim.monitor.get_event(e) is not None for e in a.evidence_event_ids)


def test_34_council_disagreement_preserved(sim):
    action, ctx = _context(sim)
    report = run(build_mock_council().deliberate(action, ctx))
    positions = {p.framework: p.recommendation for p in report.positions}
    assert positions == {F.VIRTUE_ETHICS: Recommendation.ESCALATE, F.CONSEQUENTIALISM: Recommendation.BLOCK,
                         F.DEONTOLOGY: Recommendation.BLOCK, F.CARE_ETHICS: Recommendation.ESCALATE}
    assert report.disagreement_detected and report.council_recommendation is Recommendation.ESCALATE
    assert len(report.assessments) == 4 and report.advisory is True and report.contains_mock_assessments
    assert set(report.evidence_event_ids) == {e for a in report.assessments for e in a.evidence_event_ids}
    assert report.evidence_event_ids


class _Fixed(EthicsMonitor):
    def __init__(self, framework, rec, action_id=None):
        self.framework, self.monitor_id, self._rec, self._aid = framework, f"fixed_{framework.value}", rec, action_id

    async def evaluate(self, action, workflow_context):
        return EthicalAssessment(framework=self.framework, action_id=self._aid or action.action_id,
                                 risk_level=RiskLevel.LOW, uncertainty=Uncertainty.LOW, recommendation=self._rec,
                                 explanation="test stub")


class _Broken(EthicsMonitor):
    framework, monitor_id = F.CARE_ETHICS, "broken_care"

    async def evaluate(self, action, workflow_context):
        raise TimeoutError("model did not answer")


def test_35_missing_or_failed_assessments_reported_and_never_count_as_allow(sim):
    action, ctx = _context(sim)
    council = EthicsCouncil([_Fixed(F.VIRTUE_ETHICS, Recommendation.ALLOW), _Broken(),
                             _Fixed(F.DEONTOLOGY, Recommendation.ALLOW, action_id="action_999")])
    report = run(council.deliberate(action, ctx))
    assert {f.monitor_id for f in report.failures} == {"broken_care", "fixed_deontology"}
    assert set(report.missing_frameworks) == {F.CONSEQUENTIALISM, F.DEONTOLOGY, F.CARE_ETHICS}
    assert report.council_recommendation is Recommendation.ESCALATE  # remaining ALLOW is not enough
    unanimous = run(EthicsCouncil([_Fixed(f, Recommendation.ALLOW) for f in F]).deliberate(action, ctx))
    assert unanimous.council_recommendation is Recommendation.ALLOW and not unanimous.disagreement_detected
    blocks = run(EthicsCouncil([_Fixed(f, Recommendation.BLOCK) for f in F]).deliberate(action, ctx))
    assert blocks.council_recommendation is Recommendation.BLOCK


def test_36_ethics_cannot_override_deterministic_blocks(tmp_path):
    sim = SwarmSimulator(tmp_path / "e.db", council=EthicsCouncil([_Fixed(f, Recommendation.ALLOW) for f in F]))
    sim.start_workflow("wf", "t")
    email_id = make_email(sim, "wf")
    approval_id = approve(sim, "wf", email_id)
    modify(sim, "wf", email_id, 1, recipient="other@example.test")
    r = send(sim, "wf", email_id, 2, approval_id, recipient="other@example.test")
    assert (r.decision, r.reason_code, r.executed) == (Decision.BLOCK, ReasonCode.APPROVAL_MISMATCH, False)
    record = sim.monitor.get_decision_for_action(r.action_id)
    assert record.advisory_recommendation is Decision.ALLOW and record.decision is Decision.BLOCK
    assert sim.sent_emails() == [] and sim.approvals.get(approval_id).status.value == "INVALIDATED"
    sim.close()


def test_ethics_concern_escalates_an_allow_but_never_executes(tmp_path):
    sim = SwarmSimulator(tmp_path / "e.db", council=EthicsCouncil([_Fixed(f, Recommendation.BLOCK) for f in F]))
    sim.start_workflow("wf", "t")
    email_id = make_email(sim, "wf")
    approval_id = approve(sim, "wf", email_id)
    r = send(sim, "wf", email_id, 1, approval_id)
    assert (r.decision, r.reason_code, r.executed) == (Decision.ESCALATE, ReasonCode.ETHICS_ESCALATION, False)
    assert sim.sent_emails() == [] and sim.approvals.get(approval_id).status.value == "APPROVED"
    sim.close()


def test_action_only_scope_hides_workflow_history(sim):
    action, ctx = _context(sim)
    narrow = restrict_context(ctx, ContextScope.ACTION_ONLY)
    assert narrow.scope == "ACTION_ONLY" and [e.action_id for e in narrow.history] == [action.action_id]
    assert not narrow.approvals and not narrow.resource_versions
    # Without the workflow, the history-based mock rules see nothing (illustrates RQ1's premise; not a result).
    assert run(MockDeontologicalMonitor().evaluate(action, narrow)).recommendation is Recommendation.ALLOW
