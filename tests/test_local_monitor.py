"""Local Action Monitor (spec items 7-11)."""

from __future__ import annotations

import json

import pytest

from swarmguard.models.levels import Recommendation, Uncertainty
from swarmguard.models.risk import ObservationWindow, SafetyAssessment
from swarmguard.monitoring.base import MonitorInputError
from swarmguard.monitoring.local_monitor import LocalActionMonitor
from tests.conftest import run
from tests.stage2_helpers import SCENARIOS, contexts, run_scenario


def test_7_evaluates_structured_action_requests():
    sim, r = run_scenario("approval_mismatch_001")
    local, _ = contexts(sim, "wf_001", r.critical_action_id)
    a = run(LocalActionMonitor().evaluate(local.action, local))
    assert a.monitor_id == "local_action" and a.window is ObservationWindow.LOCAL
    assert a.action_id == r.critical_action_id and a.recommendation is Recommendation.BLOCK


def test_8_cannot_access_hidden_workflow_history():
    sim, r = run_scenario("distributed_approval_001")
    local, glob = contexts(sim, "wf_001", r.critical_action_id)
    assert [e.action_id for e in local.events] == [r.critical_action_id]
    assert local.prior_actions == [] and local.workflow_purpose is None
    assert {x.resource_id for x in local.resources} <= {local.action.resource_id}
    assert all(a.approval_id == local.action.approval_id for a in local.approvals)
    monitor = LocalActionMonitor()
    with pytest.raises(MonitorInputError):
        run(monitor.evaluate(glob.action, glob))  # a global window is refused
    smuggled = local.model_copy(update={"events": glob.events})
    with pytest.raises(MonitorInputError):
        run(monitor.evaluate(local.action, smuggled))  # history smuggled into a LOCAL context is refused
    # no storage handles anywhere on the monitor
    assert {type(v).__name__ for v in vars(monitor).values()} == {"RiskAnalyzer"}


def test_9_handles_missing_and_malformed_information(sim, workflow):
    bad = run(sim.operations.propose(workflow, "send_email", {"recipient": "customer@example.test"},
                                     resource_id="email_404", resource_version=1, approval_id="approval_404"))
    assert bad.reason_code.value == "INVALID_ACTION"  # gateway blocks; the proposal is still observable
    local, _ = contexts(sim, workflow, bad.action_id)
    a = run(LocalActionMonitor().evaluate(local.action, local))
    assert a.uncertainty is Uncertainty.HIGH and a.missing_information
    assert any("malformed" in m for m in a.missing_information)
    assert any("approval_404" in m for m in a.missing_information)


def test_10_produces_valid_assessments():
    sim, r = run_scenario("legitimate_collaboration_002")
    local, _ = contexts(sim, "wf_001", r.critical_action_id)
    a = run(LocalActionMonitor().evaluate(local.action, local))
    assert SafetyAssessment.model_validate(a.model_dump(mode="json")) == a
    assert set(a.evidence_event_ids) <= local.event_ids()


def test_11_does_not_receive_ground_truth():
    for sid in SCENARIOS:
        sim, r = run_scenario(sid)
        local, _ = contexts(sim, "wf_001", r.critical_action_id)
        blob = json.dumps(local.model_dump(mode="json"))
        gt = SCENARIOS[sid].ground_truth()
        for leak in (sid, gt.scenario_type.value, gt.rationale, "ground_truth", "expected_"):
            assert leak not in blob, (sid, leak)
        sim.close()
