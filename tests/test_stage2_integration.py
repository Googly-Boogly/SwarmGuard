"""Stage 2 integration (spec items 42, 47) and ethics-council compatibility."""

from __future__ import annotations

import demo
import demo_stage2
from swarmguard.ethics.council import build_mock_council
from swarmguard.models.assessments import WorkflowContext
from swarmguard.security.workflow_intelligence import WorkflowIntelligenceEngine
from tests.conftest import run
from tests.stage2_helpers import run_scenario


def test_42_stage2_demonstration_executes(tmp_path, capsys):
    assert run(demo_stage2.main(tmp_path / "stage2.db", verbose=True)) == 0
    out = capsys.readouterr().out
    for heading in ("LOCAL ACTION MONITOR", "GLOBAL WORKFLOW MONITOR", "DETERMINISTIC AUTHORIZATION",
                    "FINAL RESULT", "Stage 2 demonstration check: PASS"):
        assert heading in out
    assert "Evidence references valid (persisted, <= checkpoint): yes" in out


def test_47_stage1_demonstration_still_passes(tmp_path, capsys):
    assert run(demo.main(tmp_path / "stage1.db", True)) == 0
    assert "Acceptance check: PASS" in capsys.readouterr().out


def test_ethics_council_receives_structured_workflow_context():
    sim, r = run_scenario("distributed_approval_001")
    action = sim.monitor.get_action(r.critical_action_id)
    ctx = sim.monitor.build_workflow_context(action, resource=sim.workspace.get(action.resource_id),
                                             approvals=sim.approvals.list_for_workflow("wf_001"))
    assert ctx.workflow_analysis is None  # Stage 1 path unchanged
    enriched = run(WorkflowIntelligenceEngine(sim.monitor, builder=sim.observation_builder()).enrich_ethics_context(ctx))
    analysis = enriched.workflow_analysis
    assert analysis is not None and analysis.data_flows and analysis.cross_agent_dependencies
    assert analysis.approval_relationships and analysis.findings
    WorkflowContext.model_validate(enriched.model_dump())  # serializable contract for Stage 3 monitors
    report = run(build_mock_council().evaluate(action, enriched))
    assert report.advisory is True and report.contains_mock_assessments
