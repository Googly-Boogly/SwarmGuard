"""Stage 2 read-only observability endpoints (spec item 45)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from swarmguard.api import create_app
from swarmguard.clock import ManualClock
from swarmguard.evaluation.experiment import Stage2ExperimentConfig
from swarmguard.evaluation.results import save_results
from swarmguard.evaluation.runner import run_stage2_experiment
from tests.conftest import run
from tests.stage2_helpers import run_scenario


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    base = tmp_path_factory.mktemp("api2")
    sim, _ = run_scenario("distributed_approval_001", path=base / "s2.db")
    run_scenario("legitimate_collaboration_001", sim=sim, workflow_id="wf_002")
    sim.close()
    results = run(run_stage2_experiment(
        Stage2ExperimentConfig(experiment_id="api_exp", scenario_ids=["approval_mismatch_001"]), clock=ManualClock()))
    save_results(results, base / "results")
    with TestClient(create_app(base / "s2.db", results_dir=base / "results")) as c:
        yield c


def test_45_workflow_risk_endpoints(client):
    risk = client.get("/workflows/wf_001/risk").json()
    assert risk["recommendation"] == "ESCALATE" and len(risk["participating_agents"]) == 3
    assert client.get("/workflows/wf_002/risk").json()["recommendation"] == "ALLOW"
    patterns = client.get("/workflows/wf_001/patterns").json()
    assert any(p["pattern_id"] == "WORKFLOW_INVARIANT_VIOLATION" for p in patterns)
    deps = client.get("/workflows/wf_001/dependencies").json()
    assert deps["cross_agent_dependencies"] and deps["data_flows"]
    events = {e["event_id"] for e in client.get("/workflows/wf_001/events").json()}
    assert {e for p in patterns for e in p["evidence_event_ids"]} <= events


def test_45_evaluation_endpoints(client):
    listing = client.get("/evaluations").json()
    assert [e["experiment_id"] for e in listing] == ["api_exp"] and listing[0]["is_research_result"] is False
    detail = client.get("/evaluations/api_exp").json()
    assert len(detail["predictions"]) == 2 and detail["metadata"]["scenario_count"] == 1
    metrics = client.get("/evaluations/api_exp/metrics").json()
    assert set(metrics["detection"]) == {"local_action", "global_workflow"}


def test_stage2_endpoints_validate_input_and_stay_read_only(client):
    for path in ("/workflows/nope/risk", "/workflows/nope/patterns", "/workflows/nope/dependencies",
                 "/evaluations/missing", "/evaluations/missing/metrics"):
        assert client.get(path).status_code == 404, path
    for path in ("/evaluations/..%2F..%2Fetc", "/evaluations/a b", "/evaluations/..%2Fapi_exp/metrics"):
        assert client.get(path).status_code in (404, 422), path
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/workflows/wf_001/risk").status_code == 405
        assert getattr(client, method)("/evaluations").status_code == 405
    for route in client.app.routes:
        assert set(getattr(route, "methods", set()) or set()) <= {"GET", "HEAD"}
