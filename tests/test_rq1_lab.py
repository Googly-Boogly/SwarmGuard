"""RQ1 research laboratory: configuration, validation, execution, persistence, metrics and raw-data access.

Monitors are evaluated on the development pilot scenarios, plus one complete held-out run that must reproduce the
frozen primary results (rq1_primary_001) exactly.
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from swarmguard.api import create_app
from swarmguard.console.api import create_console_app
from swarmguard.evaluation import rq1
from swarmguard.research import rq1_lab
from swarmguard.research.rq1_lab import (EVIDENCE_KIND, RESULT_KIND, RQ1ExperimentConfig, RQ1Laboratory, load_evidence,
                                         query_rows, validate_config)
from swarmguard.storage.database import Database

ORIGIN = {"Origin": "http://localhost:5173"}
PILOT = list(rq1.PILOT_SCENARIOS)


def _wait(client, eid, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = client.get(f"/api/research/rq1/experiments/{eid}").json()
        if record["status"] in rq1_lab.TERMINAL:
            return record
        time.sleep(0.2)
    raise AssertionError("experiment did not finish")


@pytest.fixture(scope="module")
def console(tmp_path_factory):
    app = create_console_app(tmp_path_factory.mktemp("rq1") / "console.db", local_access=True, step_delay=0)
    with TestClient(app, base_url="https://localhost") as client:
        session = client.post("/api/session/local", json={}, headers=ORIGIN).json()
        headers = {**ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        started = client.post("/api/research/rq1/experiments", json={"partition": "development", "scenario_ids": PILOT},
                              headers=headers)
        assert started.status_code == 202, started.text
        eid = started.json()["experiment_id"]
        record = _wait(client, eid)
        yield client, headers, eid, record, app


# ----------------------------------------------------------------------------- experiment


def test_rq1_selectable_with_datasets_and_recommended_settings(console):
    client, *_ = console
    overview = client.get("/api/research/rq1").json()
    assert overview["question"]["research_question_id"] == "RQ1"
    assert overview["question"]["question"].startswith("Can global workflow monitoring")
    held = next(d for d in overview["datasets"] if d["partition"] == "held_out")
    assert held["content_hash"] == rq1_lab.FROZEN_DATASET_HASH and held["matches_frozen_hash"]
    assert (held["n"], held["unsafe"], held["legitimate"]) == (24, 12, 12)
    assert overview["recommended_config"] == {**RQ1ExperimentConfig().model_dump(), "llm_mode": "none"}
    assert overview["llm_status"]["live"]["available"] is False  # no provider injected into this test server
    assert overview["recommended_config"]["partition"] == "held_out" and overview["recommended_config"]["seed"] == 0
    assert "MOCK_AUTONOMOUS" in overview["unavailable_options"]["execution_modes"]


def test_valid_configuration_runs_persists_and_records_progress(console):
    client, headers, eid, record, app = console
    assert record["status"] == "COMPLETED", record["error"]
    assert record["progress"]["completed_workflows"] == 2 and record["progress"]["failed_workflows"] == 0
    phases = [p["phase"] for p in record["progress"]["phases"]]
    assert phases[:5] == ["VALIDATION", "EXECUTION", "SCORING", "VERIFICATION", "ANALYSIS"] and phases[-1] == "COMPLETED"
    assert record["activity"] and all(a["kind"] for a in record["activity"])
    assert any(a["kind"] == "MONITOR_ASSESSMENT" for a in record["activity"])
    assert record["model_requests"] == 0 and record["estimated_cost_usd"] == 0.0
    listed = client.get("/api/research/rq1/experiments").json()
    assert any(e["experiment_id"] == eid for e in listed)
    # Terminal experiments are immutable and cannot be cancelled.
    assert client.post(f"/api/research/rq1/experiments/{eid}/cancel", json={}, headers=headers).status_code == 409


def test_results_survive_an_application_restart(console, tmp_path):
    db = Database(tmp_path / "persist.db")
    try:
        lab = RQ1Laboratory(db)
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT[:1])
        record = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        asyncio.run(lab.run(record["experiment_id"]))
    finally:
        db.close()
    reopened = Database(tmp_path / "persist.db")
    try:
        lab = RQ1Laboratory(reopened)
        lab.recover()  # a completed experiment is untouched by recovery
        stored, _ = lab.get(record["experiment_id"])
        assert stored["status"] == "COMPLETED"
        assert lab.repo.get(RESULT_KIND, record["experiment_id"]) is not None
        second = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        assert second["experiment_id"] != record["experiment_id"]
    finally:
        reopened.close()


def test_interrupted_experiment_is_recovered_as_inconclusive_not_resumed(tmp_path):
    db = Database(tmp_path / "r.db")
    try:
        lab = RQ1Laboratory(db)
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT[:1])
        record = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        lab.recover()
        stored, _ = lab.get(record["experiment_id"])
        assert stored["status"] == "INCONCLUSIVE" and "not resumed" in stored["error"]
        assert asyncio.run(lab.run(record["experiment_id"]))["status"] == "INCONCLUSIVE"
        assert lab.repo.get(RESULT_KIND, record["experiment_id"]) is None
    finally:
        db.close()


@pytest.mark.parametrize("body", [
    {"partition": "test"}, {"monitors": ["local_action"]}, {"monitors": ["local_action", "global_workflow", "ethics"]},
    {"execution_mode": "LIVE_LLM"}, {"trials": 4}, {"scenario_ids": []}, {"scenario_ids": ["a", "a"]},
    {"approver_id": "human"}, {"seed": -1}])
def test_invalid_configurations_are_rejected(console, body):
    client, headers, *_ = console
    assert client.post("/api/research/rq1/validate", json=body, headers=headers).status_code == 422
    assert client.post("/api/research/rq1/experiments", json=body, headers=headers).status_code == 422


def test_methodologically_invalid_configurations_cannot_run(console, monkeypatch):
    client, headers, *_ = console
    unknown = client.post("/api/research/rq1/validate", json={"scenario_ids": ["no_such_scenario"]}, headers=headers).json()
    assert unknown["status"] == "INVALID"
    started = client.post("/api/research/rq1/experiments", json={"scenario_ids": ["no_such_scenario"]}, headers=headers)
    assert started.status_code == 422 and "INVALID" in started.json()["detail"]
    short = client.post("/api/research/rq1/validate", json={"partition": "development", "scenario_ids": PILOT,
                                                            "max_steps_per_workflow": 2}, headers=headers).json()
    assert short["status"] == "INVALID" and next(c for c in short["checks"] if c["id"] == "limits")["status"] == "FAIL"
    monkeypatch.setenv("SWARMGUARD_MAX_RUNTIME_SECONDS", "60")
    slow = asyncio.run(validate_config(RQ1ExperimentConfig(partition="development", scenario_ids=PILOT,
                                                           max_runtime_seconds=120)))
    assert slow["status"] == "INVALID"


def test_validation_reports_warnings_not_validity_for_pilots_and_passes_the_protocol_configuration():
    pilot = asyncio.run(validate_config(RQ1ExperimentConfig(partition="development", scenario_ids=PILOT)))
    assert pilot["status"] == "WARNINGS"
    assert {c["id"] for c in pilot["checks"] if c["status"] == "WARN"} >= {"dataset", "subset"}
    primary = asyncio.run(validate_config(RQ1ExperimentConfig()))
    # The only remark on the pre-registered rules-only procedure is that no LLM will be called.
    assert primary["status"] == "WARNINGS"
    assert [c["id"] for c in primary["checks"] if c["status"] != "PASS"] == ["llm"]
    ids = {c["id"] for c in primary["checks"]}
    assert ids >= {"dataset", "schema", "labels", "monitors", "matched_snapshots", "label_isolation", "future_events",
                   "scoring", "comparability", "limits", "dataset_validity"}
    assert next(c for c in primary["checks"] if c["id"] == "matched_snapshots")["detail"].count("no monitor evaluated")
    one_class = asyncio.run(validate_config(RQ1ExperimentConfig(partition="development",
                                                                scenario_ids=["legitimate_collaboration_001"])))
    assert next(c for c in one_class["checks"] if c["id"] == "labels")["status"] == "WARN"


def _evidence(app, eid):
    from swarmguard.storage.repositories import PlatformRepository
    repo = PlatformRepository(app.state.reader)
    return {wf: load_evidence(repo, eid, wf) for wf in ("wf_001", "wf_002")}, repo


def test_both_monitors_evaluate_matching_snapshots_and_evidence_matches_the_scored_run(console):
    _, _, eid, _, app = console
    evidence, repo = _evidence(app, eid)
    result = repo.get(RESULT_KIND, eid)[0]
    official = {(c["workflow_id"], c["step"], c["configuration"]): c for c in result["results"]["checkpoints"]}
    assert result["crosscheck"] == [] and result["verification"]["checks_passed"]
    for wf, ev in evidence.items():
        for cp in ev["checkpoints"]:
            lo, gl = cp["evaluations"]["local_action"], cp["evaluations"]["global_workflow"]
            assert lo["input"]["snapshot_digest"] == gl["input"]["snapshot_digest"] == cp["snapshot_digest"]
            assert {e["event_id"] for e in lo["input"]["events"]} <= {e["event_id"] for e in gl["input"]["events"]}
            assert lo["input"]["window"] == "LOCAL" and gl["input"]["window"] == "GLOBAL"
            for cfg, e in cp["evaluations"].items():
                assert official[(wf, cp["step"], cfg)]["recommendation"] == e["output"]["recommendation"]


def test_ground_truth_and_future_events_are_isolated_from_monitor_inputs(console):
    _, _, eid, _, app = console
    evidence, repo = _evidence(app, eid)
    truths = repo.get(RESULT_KIND, eid)[0]["results"]["ground_truth"]
    for wf, ev in evidence.items():
        truth = next(t for t in truths if t["workflow_id"] == wf)
        assert "label" not in ev and "ground_truth" not in ev
        for cp in ev["checkpoints"]:
            for e in cp["evaluations"].values():
                text = json.dumps(e["input"])
                assert truth["scenario_id"] not in text and truth["description"] not in text and truth["rationale"] not in text
                assert all(x["seq"] <= e["input"]["checkpoint_seq"] for x in e["input"]["events"])
                assert e["input"]["workflow_id"] == wf and wf.startswith("wf_")


def test_gateway_decisions_are_recorded_separately_from_detection(console):
    client, _, eid, _, app = console
    evidence, _ = _evidence(app, eid)
    for ev in evidence.values():
        assert {d["action_id"] for d in ev["decisions"]} == {a["action_id"] for a in ev["actions"]}
    rows = client.get(f"/api/research/rq1/experiments/{eid}/raw", params={"page_size": 200}).json()["rows"]
    allowed_but_alerted = [r for r in rows if r["gateway_decision"] == "ALLOW" and r["alert"]]
    assert allowed_but_alerted, "monitor alerts on gateway-allowed actions must stay visible as detections"
    unsafe = client.get(f"/api/research/rq1/experiments/{eid}/raw", params={"level": "workflow", "ground_truth": "UNSAFE"}).json()
    assert {r["authorization_result"] for r in unsafe["rows"]} == {"ALLOW"}
    assert {r["outcome"] for r in unsafe["rows"]} == {"TP", "FN"}


# ----------------------------------------------------------------------------- metrics


def test_metrics_match_hand_counts_from_stored_predictions(console):
    client, _, eid, _, _ = console
    res = client.get(f"/api/research/rq1/experiments/{eid}/results").json()
    rows = client.get(f"/api/research/rq1/experiments/{eid}/raw", params={"level": "workflow", "page_size": 200}).json()["rows"]
    for cfg, name in (("local_action", "Local"), ("global_workflow", "Global")):
        mine = [r for r in rows if r["monitor"] == name]
        tp = sum(r["outcome"] == "TP" for r in mine)
        fp = sum(r["outcome"] == "FP" for r in mine)
        tn = sum(r["outcome"] == "TN" for r in mine)
        fn = sum(r["outcome"] == "FN" for r in mine)
        m = res["analysis"]["primary"][cfg]
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (tp, fp, tn, fn)
        assert m["recall"] == (tp / (tp + fn) if tp + fn else None)
        assert m["precision"] == (tp / (tp + fp) if tp + fp else None)
        assert m["false_positive_rate"] == (fp / (fp + tn) if fp + tn else None)
        p, r = m["precision"], m["recall"]
        assert m["f1"] == (pytest.approx(2 * p * r / (p + r)) if p and r else m["f1"])
    assert res["counts"]["checkpoint_evaluations"] == sum(
        2 * len(ev["checkpoints"]) for ev in _evidence(console[4], eid)[0].values())
    assert res["counts"]["workflow_predictions"] == 4
    table = {t["key"]: t for t in res["metrics"]["table"]}
    assert table["recall"]["difference"] == pytest.approx(table["recall"]["global"] - table["recall"]["local"])
    assert table["cost"]["local"] == table["cost"]["global"] == 0


def test_undefined_metrics_are_null_not_zero(tmp_path):
    db = Database(tmp_path / "u.db")
    try:
        lab = RQ1Laboratory(db)
        config = RQ1ExperimentConfig(partition="development", scenario_ids=["legitimate_collaboration_001"])
        record = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        asyncio.run(lab.run(record["experiment_id"]))
        result = lab.repo.get(RESULT_KIND, record["experiment_id"])[0]
        for cfg in ("local_action", "global_workflow"):
            assert result["analysis"]["primary"][cfg]["recall"] is None
        assert result["findings"]["decision_rule"]["satisfied"] is None
        assert "undefined" in result["findings"]["detection_performance"]
    finally:
        db.close()


def test_failed_evaluations_are_recorded_and_never_scored(tmp_path, monkeypatch):
    db = Database(tmp_path / "f.db")
    try:
        lab = RQ1Laboratory(db)
        # Run-time re-validation fails closed even if a stale validation said otherwise.
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, max_steps_per_workflow=2)
        record = lab.prepare(config, {**asyncio.run(validate_config(config)), "status": "WARNINGS"}, "tester")
        done = asyncio.run(lab.run(record["experiment_id"]))
        assert done["status"] == "FAILED" and "validation failed at run time" in done["error"]
        assert lab.repo.get(RESULT_KIND, record["experiment_id"]) is None
        # A workflow that fails during execution is recorded, the others keep their evidence, nothing is scored.
        real = rq1_lab._capture_in_thread

        def flaky(item, *args):
            if item.workflow_id == "wf_002":
                raise RuntimeError("injected monitor failure")
            return real(item, *args)
        monkeypatch.setattr(rq1_lab, "_capture_in_thread", flaky)
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT)
        record = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        done = asyncio.run(lab.run(record["experiment_id"]))
        assert done["status"] == "FAILED" and done["progress"]["failed_workflows"] == 1
        assert done["failures"] == [{"workflow_id": "wf_002", "scenario_id": done["failures"][0]["scenario_id"],
                                     "phase": "EXECUTION", "error": "RuntimeError: injected monitor failure"}]
        assert lab.repo.get(EVIDENCE_KIND, f"{record['experiment_id']}:wf_001") is not None
        assert done["headline"] is None and lab.repo.get(RESULT_KIND, record["experiment_id"]) is None
    finally:
        db.close()


def test_complete_held_out_run_reproduces_the_frozen_primary_results(tmp_path):
    db = Database(tmp_path / "primary.db")
    try:
        lab = RQ1Laboratory(db)
        config = RQ1ExperimentConfig(timing_repeats=1)
        record = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        assert record["preregistered_procedure"]
        done = asyncio.run(lab.run(record["experiment_id"]))
        assert done["status"] == "COMPLETED", done["error"]
        h = done["headline"]
        assert (h["local_tp"], h["local_fp"], h["local_fn"], h["local_tn"]) == (5, 3, 7, 9)
        assert (h["global_tp"], h["global_fp"], h["global_fn"], h["global_tn"]) == (11, 0, 1, 12)
        result = lab.repo.get(RESULT_KIND, record["experiment_id"])[0]
        assert result["verification"]["contexts_checked"] == 680 and result["verification"]["checks_passed"]
        assert result["analysis"]["paired"]["unsafe"]["only_global"] == 6
        assert result["failure_case_counts"]["LOCAL_MISSED_GLOBAL_DETECTED"] == 6
        assert result["findings"]["decision_rule"]["applies"] and result["findings"]["decision_rule"]["satisfied"]
        assert "does not prove H1" in result["findings"]["decision_rule"]["note"]
    finally:
        db.close()


# ----------------------------------------------------------------------------- raw data


def test_raw_data_pagination_filtering_and_sorting(console):
    client, _, eid, _, _ = console
    base = f"/api/research/rq1/experiments/{eid}/raw"
    everything = client.get(base, params={"page_size": 200}).json()
    total = everything["total"]
    assert total == len(everything["rows"]) and total > 10
    first, second = (client.get(base, params={"page_size": 5, "page": p}).json() for p in (1, 2))
    assert first["pages"] == -(-total // 5) and not {r["row_id"] for r in first["rows"]} & {r["row_id"] for r in second["rows"]}
    local = client.get(base, params={"monitor": "Local", "page_size": 200}).json()
    assert local["total"] == total // 2 and {r["monitor"] for r in local["rows"]} == {"Local"}
    alerts = client.get(base, params={"alert": "true", "page_size": 200}).json()
    assert all(r["recommendation"] in ("ESCALATE", "BLOCK") for r in alerts["rows"])
    combined = client.get(base, params={"monitor": "Global", "ground_truth": "SAFE", "recommendation": "ALLOW"}).json()
    assert all(r["monitor"] == "Global" and r["ground_truth"] == "SAFE" for r in combined["rows"])
    ordered = client.get(base, params={"sort": "step", "order": "desc", "page_size": 200}).json()["rows"]
    assert [r["step"] for r in ordered] == sorted((r["step"] for r in ordered), reverse=True)
    searched = client.get(base, params={"q": "send_email", "page_size": 200}).json()
    assert searched["total"] and all("send_email" in json.dumps(r) for r in searched["rows"])
    assert set(everything["facets"]) >= {"monitor", "ground_truth", "recommendation", "scenario_id", "agent_id", "alert"}
    assert client.get(base, params={"sort": "x;drop"}).status_code == 422
    assert client.get(base, params={"page_size": 100000}).status_code == 422


def test_query_rows_handles_missing_values_and_booleans():
    rows = [{"a": 2, "b": True}, {"a": None, "b": False}, {"a": 1, "b": True}]
    out = query_rows(rows, filters={"b": "true"}, query=None, sort="a", descending=False, page=1, page_size=10)
    assert [r["a"] for r in out["rows"]] == [1, 2] and out["facets"] == {}


def test_record_detail_matches_persisted_monitor_input_and_output(console):
    client, _, eid, _, app = console
    evidence, _ = _evidence(app, eid)
    cp = evidence["wf_001"]["checkpoints"][-1]
    detail = client.get(f"/api/research/rq1/experiments/{eid}/raw/wf_001/{cp['step']}/global_workflow").json()
    stored = cp["evaluations"]["global_workflow"]
    assert detail["original"]["monitor_input"] == stored["input"]
    assert detail["original"]["monitor_output"] == stored["output"]
    assert detail["monitor_output"]["recommendation"] == stored["output"]["recommendation"]
    assert detail["monitor_input"]["window"] == "GLOBAL" and detail["monitor_input"]["events"] == stored["input"]["events"]
    assert detail["ground_truth"]["label"] in ("SAFE", "UNSAFE") and detail["ground_truth"]["description"]
    assert detail["authorization"]["action_id"] == cp["action_id"]
    local = client.get(f"/api/research/rq1/experiments/{eid}/raw/wf_001/{cp['step']}/local_action").json()
    assert local["monitor_input"]["window"] == "LOCAL" and "workflow_history" in local["monitor_input"]["outside_window"]
    assert client.get(f"/api/research/rq1/experiments/{eid}/raw/wf_001/999/local_action").status_code == 404
    assert client.get(f"/api/research/rq1/experiments/{eid}/raw/wf_001/1/ethics").status_code == 422


def test_scenarios_workflows_comparisons_failures_findings_and_metadata(console):
    client, _, eid, _, _ = console
    base = f"/api/research/rq1/experiments/{eid}"
    scenarios = client.get(f"{base}/scenarios").json()
    assert {s["scenario_id"] for s in scenarios} == set(PILOT)
    detail = client.get(f"{base}/scenarios/{PILOT[0]}").json()
    assert detail["actions"] and all(a["decision"] for a in detail["actions"]) and detail["permissions"]
    wf = client.get(f"{base}/workflows/wf_001").json()
    assert wf["graph"]["nodes"] and all(isinstance(e["causal"], bool) for e in wf["graph"]["edges"])
    comparison = client.get(f"{base}/comparisons/wf_001").json()
    assert comparison["steps"] and {"local", "global", "difference", "differs"} <= set(comparison["steps"][0])
    assert all(s["local"]["scope"]["events"] <= s["global"]["scope"]["events"] for s in comparison["steps"])
    failures = client.get(f"{base}/failures").json()
    assert sum(failures["counts"].values()) == len(PILOT)
    findings = client.get(f"{base}/findings").json()
    assert set(findings["copy"]) == {"results_summary", "metrics_table", "methodology", "limitations"}
    assert not findings["decision_rule"]["applies"] and "orientation only" in findings["decision_rule"]["note"]
    meta = client.get(f"{base}/metadata").json()
    assert meta["dataset"]["content_hash"] and meta["scoring"]["protocol_version"] == rq1.PROTOCOL_VERSION
    csv_text = client.get(f"{base}/download", params={"level": "workflow", "format": "csv"}).text
    assert csv_text.splitlines()[0].startswith("alert_checkpoints") and len(csv_text.splitlines()) == 5


# ----------------------------------------------------------------------------- security


def test_research_data_requires_an_authenticated_researcher_and_csrf(console, tmp_path):
    _, _, eid, _, _ = console
    app = create_console_app(tmp_path / "other.db", local_access=True, step_delay=0)
    with TestClient(app, base_url="https://localhost") as anonymous:
        for path in ("/api/research/rq1", f"/api/research/rq1/experiments/{eid}/raw",
                     f"/api/research/rq1/experiments/{eid}/raw/wf_001/1/local_action",
                     f"/api/research/rq1/experiments/{eid}/findings"):
            assert anonymous.get(path).status_code == 401
        assert anonymous.post("/api/research/rq1/experiments", json={}, headers=ORIGIN).status_code == 401
        session = anonymous.post("/api/session/local", json={}, headers=ORIGIN).json()
        assert anonymous.post("/api/research/rq1/experiments", json={}, headers=ORIGIN).status_code == 403
        assert anonymous.post("/api/research/rq1/validate", json={},
                              headers={"Origin": "https://evil.example", "X-CSRF-Token": session["csrf_token"]}).status_code == 403


def test_observation_api_exposes_no_rq1_labels(tmp_path):
    from swarmguard.agents.simulator import SwarmSimulator
    SwarmSimulator(str(tmp_path / "obs.db")).close()
    app = create_app(str(tmp_path / "obs.db"))
    with TestClient(app) as client:
        assert client.get("/api/research/rq1").status_code == 404
        assert client.get("/api/research/rq1/experiments/rq1_experiment_001/raw").status_code == 404
        assert all("rq1" not in getattr(r, "path", "") for r in app.routes)


def test_rq1_lab_adds_no_network_or_execution_path():
    import ast
    import inspect
    from swarmguard.console import rq1_routes
    for module in (rq1_lab, rq1_routes):
        tree = ast.parse(inspect.getsource(module))
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not names & {"smtplib", "socket", "requests", "httpx", "openai"}
        assert not any(m and m.startswith(("swarmguard_providers", "swarmguard_services")) for m in modules)
        assert "MockToolExecutor" not in inspect.getsource(module)
