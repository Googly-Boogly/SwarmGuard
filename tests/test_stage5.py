"""Stage 5 behavioral, boundary, measurement and reproducibility regressions."""
from __future__ import annotations
import asyncio
from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from swarmguard.adversarial.adaptive_controller import AdaptiveController
from swarmguard.adversarial.constraints import validate_candidate
from swarmguard.adversarial.scenario_generator import ScenarioGenerator, CATALOG
from swarmguard.adversarial.scenario_mutator import ScenarioMutator
from swarmguard.adversarial.novelty_detector import NoveltyDetector
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.evaluation.adaptive_runner import evaluate_candidate
from swarmguard.evaluation.experiment_manifest import export_results
from swarmguard.evaluation.failure_analysis import analyze_failures
from swarmguard.evaluation.robustness import vary_observation
from swarmguard.evaluation.statistical_analysis import summarize_trials, wilson_interval
from swarmguard.evaluation.scenarios.adaptive_coordination import ParameterizedScenario
from swarmguard.ethics.synthetic import rules_responder
from swarmguard.llm.mock_provider import MockLLMProvider, MockScript, MockStep, MockBehavior
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider
from swarmguard.models.adversarial import ResearchConfig, ScenarioDefinition
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from swarmguard.monitoring.information_flow import information_flow
from swarmguard.monitoring.coordination_patterns import coordination_patterns
from swarmguard.monitoring.temporal_analysis import TemporalAnalysis
from swarmguard.monitoring.evidence_validator import validate_evidence, validate_context
from swarmguard.security.risk_analyzer import EvidenceIntegrityError
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ResearchRepository


def run(coro):
    return asyncio.run(coro)


def provider(script=None, max_requests=200):
    return GuardedProvider(MockLLMProvider(script, responder=rules_responder), BudgetPolicy(max_requests=max_requests))


def fixture_context(template="distributed_approval_001"):
    sim = SwarmSimulator(clock=SteppingClock())
    definition = ScenarioDefinition(scenario_id="candidate", template_id=template)
    result = run(ParameterizedScenario(definition).run(sim, "wf"))
    builder = sim.observation_builder()
    snapshot = builder.snapshot_for_action(result.critical_action_id)
    return sim, builder.global_(snapshot, result.critical_action_id)


@pytest.mark.parametrize("extra", ["policy", "approval", "identity", "tool", "shell", "sql", "ground_truth"])
def test_generated_schema_cannot_express_authority(extra):
    with pytest.raises(ValidationError):
        ScenarioDefinition(scenario_id="candidate", template_id="distributed_approval_001", **{extra: "forged"})


@pytest.mark.parametrize("field,value", [("agent_count",13),("max_steps",61),("max_steps",True),
    ("environment","production"),("background_rounds",5),("observation","arbitrary")])
def test_invalid_definitions_rejected(field,value):
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate({"scenario_id":"candidate","template_id":"distributed_approval_001",field:value})


def test_generator_does_not_read_or_supply_ground_truth(monkeypatch):
    from swarmguard.evaluation.scenarios.base import Scenario
    monkeypatch.setattr(Scenario, "ground_truth", lambda self: (_ for _ in ()).throw(AssertionError("label accessed")))
    candidates = ScenarioGenerator().generate(ResearchConfig())
    assert candidates and all(validate_candidate(c, ResearchConfig()) == [] for c in candidates)
    assert all("ground_truth" not in c.model_dump() for c in candidates)
    assert all(c.partition == "adaptive_search" for c in candidates)


@pytest.mark.parametrize("template", ["transformed_disclosure_001","expired_approval_001","unknown","approval_mismatch_001"])
def test_adaptive_validator_excludes_other_partitions(template):
    assert validate_candidate(ScenarioDefinition(scenario_id="c",template_id=template),ResearchConfig())


def test_partition_catalog_covers_real_library_and_is_disjoint():
    from swarmguard.evaluation.scenarios import stage2_scenarios
    ids = {s.scenario_id for s in stage2_scenarios()}
    assert ids == set(CATALOG) - {"monitor_manipulation_001"}
    sets = [{k for k,v in CATALOG.items() if v[0] == partition} for partition in ("development","adaptive_search","held_out")]
    assert not any(sets[i] & sets[j] for i in range(3) for j in range(i))
    with pytest.raises(ValidationError):
        ResearchConfig(partition="held_out")


@pytest.mark.parametrize("n",[3,5,8,12])
def test_variable_agents_have_unique_trusted_identities_and_permissions(n):
    sim = SwarmSimulator(agent_count=n)
    try:
        sim.start_workflow("wf","neutral")
        ids = sim.registered_agent_ids
        assert len(ids) == len(set(ids)) == n
        assert all("wf" in sim.agent_profile(a)["authorized_workflows"] for a in ids)
        for agent in sim.additional_agents.values():
            assert run(agent.read("wf","read_document","doc_service_faq")).executed
            rejected = run(agent.propose("wf","prepare_email",{"recipient":"a@example.test","subject":"s","body":"b"}))
            assert rejected.decision is Decision.BLOCK and not rejected.executed
            assert not run(agent.read("wf","read_customer_record","cust_001")).executed
    finally:
        sim.close()


def test_gateway_step_budget_allows_then_blocks_and_audits():
    sim=SwarmSimulator(max_workflow_proposals=1)
    try:
        sim.start_workflow("wf","neutral")
        first=run(sim.research.read("wf","read_document","doc_service_faq"))
        second=run(sim.research.read("wf","read_document","doc_service_faq"))
        assert first.executed
        assert second.reason_code is ReasonCode.EXECUTION_LIMIT_EXCEEDED and not second.executed
        assert len(sim.monitor.get_decisions("wf")) == 2
        assert sim.monitor.get_decision_for_action(second.action_id).decision is Decision.BLOCK
    finally:
        sim.close()


@pytest.mark.parametrize("template",["legitimate_collaboration_001","task_decomposition_001"])
def test_mutations_change_real_execution_with_valid_permissions(template):
    parent=ScenarioDefinition(scenario_id="parent",template_id=template)
    mutations=ScenarioMutator().variations(parent,1)
    assert len({m.scenario_id for m in mutations}) == len(mutations)
    for mutation in mutations:
        assert mutation.parent_scenario_id == parent.scenario_id
        assert not validate_candidate(mutation,ResearchConfig())
        sim=SwarmSimulator(agent_count=mutation.agent_count,max_workflow_proposals=60)
        try:
            executed=run(ParameterizedScenario(mutation).run(sim,"wf"))
            assert executed.critical_action_id
            assert all(d.decision is Decision.ALLOW for d in sim.monitor.get_decisions("wf"))
            truth=ParameterizedScenario(mutation).ground_truth()
            assert truth.scenario_id == mutation.scenario_id
            if mutation.approval_delay_reads:
                timeline=sim.monitor.get_workflow_history("wf")
                requested=next(e.seq for e in timeline if e.event_type.value=="APPROVAL_REQUESTED")
                granted=next(e.seq for e in timeline if e.event_type.value=="APPROVAL_GRANTED")
                assert sum(requested<e.seq<granted and e.event_type.value=="RESOURCE_READ" for e in timeline)==2
        finally:
            sim.close()


def test_novelty_ignores_names_and_identifiers_but_compares_structure():
    actions=[{"action_id":"a","agent_id":"one","tool":"read_document","resource_id":"r","parent_action_ids":[],"parameters":{}}]
    detector=NoveltyDetector()
    assert detector.classify(actions,[])["classification"] == "STRUCTURALLY_DISTINCT"
    renamed=[{**actions[0],"action_id":"b","agent_id":"different","resource_id":"other"}]
    assert detector.classify(renamed,[])["classification"] == "DUPLICATE"
    assert detector.classify([{**actions[0],"parameters":{"content":"changed"}}],[])["classification"] == "MINOR_VARIATION"
    assert detector.classify([*actions,{"action_id":"c","agent_id":"two","tool":"send_message","resource_id":None,"parent_action_ids":["a"],"parameters":{}}],[])["classification"] == "STRUCTURALLY_DISTINCT"


@pytest.mark.parametrize("variant",["original","incomplete","reordered","misleading","contradictory","injection"])
def test_robustness_changes_only_experimental_observation(variant):
    sim,ctx=fixture_context()
    try:
        history=deepcopy(sim.monitor.get_workflow_history("wf"))
        original=ctx.model_dump()
        varied,metadata=vary_observation(ctx,variant)
        assert ctx.model_dump()==original
        assert sim.monitor.get_workflow_history("wf")==history
        assert all(e.seq<=ctx.checkpoint_seq for e in varied.events)
        assert "ground_truth" not in varied.model_dump()
        assert metadata["source_snapshot_digest"]==ctx.snapshot_digest
        assert metadata["applied"]
        assessment=run(GlobalWorkflowMonitor().evaluate(varied.action,varied))
        validate_evidence(varied,assessment.evidence_event_ids)
        if variant=="incomplete":
            assert metadata["withheld"] and len(varied.resources)<len(ctx.resources)
        if variant=="reordered":
            assert {e.event_id for e in varied.events} == {e.event_id for e in ctx.events}
        if variant in ("misleading","contradictory","injection"):
            assert metadata["supplements"]
    finally:
        sim.close()


def test_evidence_validation_rejects_future_and_invented_references():
    sim,ctx=fixture_context()
    try:
        with pytest.raises(EvidenceIntegrityError):
            validate_evidence(ctx,["invented"])
        altered=ctx.model_copy(update={"events":[ctx.events[0].model_copy(update={"seq":ctx.checkpoint_seq+1})]})
        with pytest.raises(EvidenceIntegrityError):
            validate_context(altered)
    finally:
        sim.close()


def test_ordinary_coordination_is_observed_not_malicious():
    sim,ctx=fixture_context("legitimate_collaboration_001")
    try:
        signals=coordination_patterns(ctx)
        assert signals and all(f["category"]=="ORDINARY_COLLABORATION" for f in signals)
        assert run(GlobalWorkflowMonitor().evaluate(ctx.action,ctx)).recommendation.value=="ALLOW"
        assert all(set(f["evidence_event_ids"])<=ctx.event_ids() for f in signals)
    finally:
        sim.close()


def test_synthetic_information_labels_provenance_and_restricted_destination():
    sim,ctx=fixture_context()
    try:
        flows=information_flow(ctx)
        assert flows and any(f["restricted_destination"] for f in flows)
        assert any("PROTECTED_SYNTHETIC" in f["field_labels"].values() for f in flows)
        assert all(f["provenance"] for f in flows)
    finally:
        sim.close()


def test_temporal_analysis_preserves_explicit_dependencies():
    sim,ctx=fixture_context()
    try:
        temporal=TemporalAnalysis(sim.monitor.get_workflow_history("wf"))
        proposal=next(e for e in ctx.events if e.action_id==ctx.action.action_id)
        assert all(e.seq<proposal.seq for e in temporal.get_preceding_events(proposal.event_id))
        assert all(e.seq>proposal.seq for e in temporal.get_following_events(proposal.event_id))
        assert temporal.get_first_prohibited_proposal("wf",{ctx.action.action_id}).event_id==proposal.event_id
        assert temporal.get_first_prohibited_execution("wf",{ctx.action.action_id}) is not None
        assert all(e.event_id!=proposal.event_id for e in temporal.dependencies(proposal.event_id))
    finally:
        sim.close()


def test_budget_failures_are_not_safe_monitor_predictions():
    config=ResearchConfig(max_iterations=1)
    row=run(evaluate_candidate(ScenarioDefinition(scenario_id="c",template_id="distributed_approval_001"),config,provider(max_requests=1),"wf"))
    assert row["predictions"]["GENERAL_COUNCIL"] is None
    assert any(f["category"]=="MODEL_OUTPUT_FAILURE" for f in analyze_failures(row))
    assert row["feedback"]["prohibited_outcome_occurred"] is True


def test_bounded_adaptive_loop_records_feedback_and_refines_candidates(tmp_path):
    db=Database(tmp_path/"research.db")
    try:
        config=ResearchConfig(max_iterations=3,category="legitimate_collaboration",compare_all=False)
        ctl=AdaptiveController(db,clock=SteppingClock())
        result=run(ctl.run(config))
        rows=ctl.repo.candidates(result["run_id"])
        assert result["generated"]==result["validated"]==result["executed"]==3
        assert result["status"]=="COMPLETED" and result["failed"]==0
        assert rows[1]["definition"]["parent_scenario_id"]
        assert all(r["feedback"]["legitimate_objective_completed"] for r in rows)
        assert result["prohibited_outcomes"]==0
        assert all(r["predictions"]["GLOBAL_MONITOR"] is False for r in rows)
        assert export_results(ctl.repo,result["run_id"],tmp_path).joinpath("candidates.json").is_file()
        with pytest.raises(FileExistsError):
            export_results(ctl.repo,result["run_id"],tmp_path)
        with pytest.raises(ValueError):
            run(ctl.run(config,run_id=result["run_id"]))
    finally:
        db.close()


def test_mock_replay_predictions_and_trajectories_reproducible():
    outputs=[]
    for _ in range(2):
        db=Database()
        try:
            ctl=AdaptiveController(db,clock=SteppingClock())
            result=run(ctl.run(ResearchConfig(max_iterations=2,compare_all=False)))
            outputs.append([(r["definition"],r["predictions"],r["events"],r["feedback"]) for r in ctl.repo.candidates(result["run_id"])])
        finally:
            db.close()
    assert outputs[0]==outputs[1]


def test_run_cancellation_and_no_restart_recovery(tmp_path):
    from swarmguard.console.research import ResearchJobs
    db=Database(tmp_path/"jobs.db")
    try:
        clock=SteppingClock()
        ctl=AdaptiveController(db,clock=clock)
        config=ResearchConfig(max_iterations=2,compare_all=False)
        prepared=ctl.prepare(config)
        ctl.cancel(prepared["run_id"])
        result=run(ctl.run(config,run_id=prepared["run_id"]))
        assert result["status"]=="CANCELLED" and result["executed"]==0
        abandoned=ctl.prepare(config)
        ResearchJobs(db,clock,asyncio.Semaphore(1),"test")
        assert ctl.repo.get(abandoned["run_id"])[0]["status"]=="INTERRUPTED"
    finally:
        db.close()


@pytest.mark.parametrize("variable,value",[("SWARMGUARD_MAX_AGENTS","2"),("SWARMGUARD_MAX_ADAPTIVE_ITERATIONS","1"),
    ("SWARMGUARD_MAX_WORKFLOW_STEPS","1"),("SWARMGUARD_MAX_LLM_REQUESTS","1"),("SWARMGUARD_MAX_RUNTIME_SECONDS","1"),
    ("SWARMGUARD_MAX_CONCURRENT_REQUESTS","1")])
def test_server_limits_override_researcher_commands(monkeypatch,variable,value):
    monkeypatch.setenv(variable,value)
    db=Database()
    try:
        with pytest.raises(ValueError):
            AdaptiveController(db).prepare(ResearchConfig())
        assert ResearchRepository(db).list()==[]
    finally:
        db.close()


def test_live_requires_authorization_pricing_and_provider():
    with pytest.raises(ValidationError):
        ResearchConfig(execution_mode="openai")
    db=Database()
    try:
        with pytest.raises(ValueError):
            AdaptiveController(db).prepare(ResearchConfig(execution_mode="openai",live_authorized=True,max_cost_usd=1.0))
    finally:
        db.close()


def test_metrics_keep_undefined_and_paired_samples():
    metrics=summarize_trials([])
    assert metrics["detection"]=={} and metrics["comparison_count"]==0
    assert wilson_interval(0,0) is None
    interval=wilson_interval(50,100)
    assert interval["low"]<0.5<interval["high"]
    with pytest.raises(ValueError):
        wilson_interval(4,3)


def test_research_persistence_cas_conflict_cannot_overwrite():
    db=Database()
    try:
        ctl=AdaptiveController(db,clock=SteppingClock())
        record=ctl.prepare(ResearchConfig())
        assert record["manifest"]["monitor_version"]=="stage5-rules-v2"
        ctl.repo.save(record,expected_version=1,actor="test",occurred_at="test")
        with pytest.raises(RuntimeError):
            ctl.repo.save(record,expected_version=1,actor="test",occurred_at="test")
        assert ctl.repo.get(record["run_id"])[1]==2
    finally:
        db.close()


@pytest.mark.parametrize("n",[3,5,8,12])
def test_scaling_executes_actual_public_work(n):
    config=ResearchConfig(kind="scaling",compare_all=False)
    row=run(evaluate_candidate(ScenarioDefinition(scenario_id="c",template_id="legitimate_collaboration_001",agent_count=n),config,provider(),"wf"))
    assert row["evaluation_valid"] and row["scaling"]["agent_count"]==n
    assert len(row["scaling"]["identities"])==n
    assert row["scaling"]["tool_proposals"]==len(row["actions"])
    assert row["scaling"]["messages"]>=max(1,n-3)
    assert row["scaling"]["execution_ms"]>0
    assert row["feedback"]["legitimate_objective_completed"]


@pytest.mark.parametrize("path,kind",[("adversarial","adaptive"),("benchmarks","benchmark"),("scaling","scaling")])
def test_research_browser_authentication_csrf_and_strict_commands(tmp_path,path,kind):
    from fastapi.testclient import TestClient
    from swarmguard.console.api import create_console_app
    from swarmguard.console.auth import password_hash
    app=create_console_app(tmp_path/"console.db",encoded_password=password_hash("stage5-test-password"),secure_cookie=False)
    with TestClient(app) as client:
        endpoint=f"/api/research/{path}/runs"
        body={"kind":kind,"max_iterations":1,"compare_all":False}
        origin={"Origin":"http://localhost:5173"}
        assert client.post(endpoint,json=body,headers=origin).status_code==401
        session=client.post("/api/session",json={"password":"stage5-test-password"},headers=origin).json()
        assert client.post(endpoint,json=body,headers=origin).status_code==403
        headers={**origin,"X-CSRF-Token":session["csrf_token"]}
        for forbidden in ("policy","agent_id","approval_id","command","sql"):
            assert client.post(endpoint,json={**body,forbidden:"forged"},headers=headers).status_code==422
        assert client.post(endpoint,json={**body,"execution_mode":"openai","live_authorized":True,"max_cost_usd":1.0},headers=headers).status_code==422
        created=client.post(endpoint,json=body,headers=headers)
        assert created.status_code==202,created.text
        rid=created.json()["run_id"]
        import time
        for _ in range(200):
            record=client.get(f"/api/research/runs/{rid}").json()
            if record["status"] not in {"QUEUED","RUNNING"}:
                break
            time.sleep(.01)
        assert record["status"]=="COMPLETED",record
        candidates=client.get(f"/api/research/runs/{rid}/candidates").json()
        assert len(candidates)==1 and candidates[0]["events"] and candidates[0]["assessments"]
        exported=client.get(f"/api/research/runs/{rid}/export").json()
        assert exported["candidates"]==candidates
        assert exported["manifest"]["executed"]==1


def test_invalid_candidate_rejection_is_persisted(monkeypatch):
    bad=ScenarioDefinition(scenario_id="bad",template_id="expired_approval_001")
    monkeypatch.setattr(ScenarioGenerator,"generate",lambda self,cfg:[bad])
    db=Database()
    try:
        ctl=AdaptiveController(db,clock=SteppingClock())
        result=run(ctl.run(ResearchConfig(max_iterations=1)))
        assert result["generated"]==result["rejected"]==1 and result["executed"]==0
        candidate=ctl.repo.candidates(result["run_id"])[0]
        assert not candidate["validation"]["validated"] and candidate["validation"]["errors"]
    finally:
        db.close()


def test_adaptive_feedback_alternates_exploration_and_refinement():
    db=Database()
    try:
        ctl=AdaptiveController(db,clock=SteppingClock())
        result=run(ctl.run(ResearchConfig(max_iterations=2,compare_all=False)))
        first,second=ctl.repo.candidates(result["run_id"])
        assert second["definition"]["parent_scenario_id"]==first["scenario_id"]
        assert second["definition"]["mutation_type"]!="INITIAL"
        assert result["pool_generated"]>result["generated"]
    finally:
        db.close()


def test_timeout_preserves_unscored_failed_candidate(monkeypatch):
    import swarmguard.adversarial.adaptive_controller as module
    async def slow(*args,**kwargs):
        await asyncio.sleep(2)
    monkeypatch.setattr(module,"evaluate_candidate",slow)
    db=Database()
    try:
        ctl=AdaptiveController(db,clock=SteppingClock())
        result=run(ctl.run(ResearchConfig(max_runtime_seconds=1,max_iterations=1,compare_all=False)))
        assert result["status"]=="TIMED_OUT" and result["failed"]==1
        candidate=ctl.repo.candidates(result["run_id"])[0]
        assert not candidate["evaluation_valid"] and "predictions" not in candidate
    finally:
        db.close()


def test_held_out_benchmark_does_not_mutate_partitions_or_use_adaptive_feedback():
    db=Database()
    try:
        config=ResearchConfig(kind="benchmark",partition="held_out",max_iterations=4,compare_all=False)
        ctl=AdaptiveController(db,clock=SteppingClock())
        result=run(ctl.run(config))
        rows=ctl.repo.candidates(result["run_id"])
        assert result["status"]=="COMPLETED" and len(rows)==4
        assert all(r["definition"]["partition"]=="held_out" and r["definition"]["parent_scenario_id"] is None for r in rows)
        assert all(r["validation"]["validated"] for r in rows)
    finally:
        db.close()


def test_metrics_report_correlated_errors_and_independent_interventions():
    row=run(evaluate_candidate(ScenarioDefinition(scenario_id="c",template_id="transformed_disclosure_001",partition="held_out"),
        ResearchConfig(kind="benchmark",partition="held_out"),provider(),"wf"))
    metrics=summarize_trials([row])
    assert metrics["comparison_count"]==21
    assert metrics["correlated_monitor_failures"]
    assert any(k.startswith("SPECIALIZED_ETHICS_COUNCIL:") for k in metrics["per_framework"])
    assert metrics["operational"]["GENERAL_COUNCIL"]["model_calls"]==4
    assert metrics["operational"]["GENERAL_COUNCIL"]["input_tokens"] is None
    assert metrics["operational"]["GENERAL_COUNCIL"]["estimated_cost_usd"]==0.0
    assert all(p["n"]==1 for p in metrics["paired"])


def test_development_benchmark_includes_self_starting_approval_regression():
    db=Database()
    try:
        ctl=AdaptiveController(db,clock=SteppingClock())
        result=run(ctl.run(ResearchConfig(kind="benchmark",partition="development",max_iterations=8,compare_all=False)))
        assert result["status"]=="COMPLETED" and result["executed"]==5 and result["failed"]==0
        row=next(r for r in ctl.repo.candidates(result["run_id"]) if r["definition"]["template_id"]=="approval_mismatch_001")
        assert row["feedback"]["gateway_blocked"] and not row["feedback"]["prohibited_outcome_occurred"]
        assert row["decisions"][-1]["reason_code"]=="APPROVAL_MISMATCH"
    finally:
        db.close()


def test_llm_assisted_definition_is_data_only_and_requires_trusted_validation():
    # An explicitly synthetic provider tests the live-generation interface offline;
    # the controller itself rejects this provider for a live research run.
    parent=ScenarioDefinition(scenario_id="candidate",template_id="distributed_approval_001")
    raw=MockLLMProvider(responder=lambda system,user,tags:parent.model_dump())
    guarded=GuardedProvider(raw,BudgetPolicy(max_requests=2))
    config=ResearchConfig(execution_mode="openai",live_authorized=True,max_cost_usd=1.0)
    generated=run(ScenarioGenerator().propose(config,guarded,parent,[]))
    assert generated==parent and not validate_candidate(generated,config)
    import json
    sent=json.loads(raw.calls[0]["user_content"])
    assert all(CATALOG[t][0]=="adaptive_search" for t in sent["allowed_templates"])
    assert "ground_truth" not in sent and "held_out" not in json.dumps(sent)
    invalid=parent.model_copy(update={"template_id":"transformed_disclosure_001"})
    raw.responder=lambda system,user,tags:invalid.model_dump()
    generated=run(ScenarioGenerator().propose(config,guarded,parent,[]))
    assert validate_candidate(generated,config)
    assert guarded.ledger.attempts==2
