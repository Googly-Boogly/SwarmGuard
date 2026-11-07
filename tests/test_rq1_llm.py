"""RQ1 LLM arm: one LLM safety monitor on the LOCAL vs. GLOBAL observation of the same snapshot.

Offline only: the mock provider, or the real OpenAI provider class driven by a fake client (tests/rq1_fake_openai.py).
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from swarmguard.console.api import create_console_app
from swarmguard.ethics.prompts import system_prompt
from swarmguard.evaluation import rq1
from swarmguard.llm.configuration import LLMConfigurationError
from swarmguard.models.assessments import EthicalFramework
from swarmguard.research import rq1_llm
from swarmguard.research.rq1_lab import (EVIDENCE_KIND, RESULT_KIND, RQ1ExperimentConfig, RQ1Laboratory, load_evidence)
from swarmguard.storage.database import Database
from swarmguard_providers.rq1 import make_rq1_llm
from tests.rq1_fake_openai import FakeOpenAI

ORIGIN = {"Origin": "http://localhost:5173"}
PILOT = list(rq1.PILOT_SCENARIOS)
LIVE_ENV = {"SWARMGUARD_CONSOLE_LIVE_LLM": "1", "OPENAI_API_KEY": "sk-test-not-real", "SWARMGUARD_OPENAI_MODEL": "fake-model",
            "SWARMGUARD_PRICE_INPUT_USD_PER_MTOK": "1.0", "SWARMGUARD_PRICE_OUTPUT_USD_PER_MTOK": "4.0",
            "SWARMGUARD_MAX_EVALUATION_COST_USD": "5", "SWARMGUARD_MAX_LLM_REQUESTS": "1000"}


def _lab(env, client=None, path=":memory:"):
    factory, status = make_rq1_llm(env, client=client)
    db = Database(path)
    return db, RQ1Laboratory(db, llm_factory=factory, llm_status=status)


def _run(lab, config):
    validation = asyncio.run(lab.validate(config))
    record = lab.prepare(config, validation, "tester")
    return validation, asyncio.run(lab.run(record["experiment_id"]))


def test_live_mode_is_unavailable_without_operator_opt_in_pricing_and_credentials():
    for env, needle in (({k: v for k, v in LIVE_ENV.items() if k != "SWARMGUARD_CONSOLE_LIVE_LLM"}, "SWARMGUARD_CONSOLE_LIVE_LLM=1"),
                        ({k: v for k, v in LIVE_ENV.items() if "PRICE" not in k}, "PRICE"),
                        ({k: v for k, v in LIVE_ENV.items() if k != "OPENAI_API_KEY"}, "OPENAI_API_KEY"),
                        ({**LIVE_ENV, "SWARMGUARD_PRICE_INPUT_USD_PER_MTOK": "0"}, "positive")):
        factory, status = make_rq1_llm(env, client=FakeOpenAI())
        live = status()["live"]
        assert not live["available"] and any(needle in r for r in live["reasons"])
        assert "sk-test" not in json.dumps(status())
        with pytest.raises(LLMConfigurationError):
            factory("live", 10, 1.0)
        db, lab = _lab(env, FakeOpenAI())
        try:
            config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="live",
                                         confirm_paid_calls=True, llm_max_cost_usd=1.0)
            validation = asyncio.run(lab.validate(config))
            assert validation["status"] == "INVALID"
            with pytest.raises(ValueError, match="INVALID"):
                lab.prepare(config, validation, "tester")
        finally:
            db.close()


@pytest.mark.parametrize("body", [
    {"llm_mode": "live"}, {"llm_mode": "live", "confirm_paid_calls": True},
    {"llm_mode": "live", "llm_max_cost_usd": 1.0}, {"llm_mode": "mock", "confirm_paid_calls": True},
    {"llm_mode": "gpt"}, {"llm_checkpoints": "random"}, {"llm_max_requests": 0}, {"llm_mode": "live", "confirm_paid_calls": True, "llm_max_cost_usd": -1}])
def test_invalid_llm_configurations_are_rejected(body):
    with pytest.raises(ValueError):
        RQ1ExperimentConfig.model_validate(body)


def test_mock_llm_arm_calls_the_monitor_at_every_selected_checkpoint_and_stays_labelled_synthetic():
    db, lab = _lab({})
    try:
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="mock", llm_checkpoints="all")
        validation, done = _run(lab, config)
        assert validation["status"] == "WARNINGS"
        assert next(c for c in validation["checks"] if c["id"] == "llm")["status"] == "WARN"
        assert done["status"] == "COMPLETED", done["error"]
        expected = validation["llm_estimate"]["calls"]
        ev_steps = sum(len(load_evidence(lab.repo, done["experiment_id"], wf)["checkpoints"]) for wf in ("wf_001", "wf_002"))
        assert expected == 2 * ev_steps  # policy "all": both windows at every checkpoint
        assert done["llm_usage"]["attempts"] == expected and done["llm_usage"]["succeeded"] == expected
        assert done["llm"]["is_synthetic"] and done["llm"]["provider"] == "mock"
        result = lab.repo.get(RESULT_KIND, done["experiment_id"])[0]
        assert result["findings"]["llm_performance"].startswith("[SYNTHETIC MOCK")
        rows = [r for r in result["checkpoint_rows"] if r["configuration"] in rq1_llm.LLM_CONFIGS]
        assert len(rows) == expected and all(r["is_synthetic"] for r in rows)
        ev = load_evidence(lab.repo, done["experiment_id"], "wf_001")
        for cp in ev["checkpoints"]:
            a = cp["llm"]["llm_global"]["assessment"]
            assert a["explanation"].startswith("[SYNTHETIC MOCK") and a["is_mock"]
    finally:
        db.close()


def test_live_llm_arm_sends_paired_requests_through_the_openai_provider_with_isolated_inputs():
    fake = FakeOpenAI()
    db, lab = _lab(LIVE_ENV, fake)
    try:
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="live", llm_checkpoints="outbound",
                                     confirm_paid_calls=True, llm_max_cost_usd=2.0, llm_max_requests=100)
        validation, done = _run(lab, config)
        assert next(c for c in validation["checks"] if c["id"] == "llm")["status"] == "PASS"
        assert done["status"] == "COMPLETED", done["error"]
        calls = validation["llm_estimate"]["calls"]
        assert len(fake.calls) == calls == done["llm_usage"]["attempts"] and calls > 0
        assert not done["llm"]["is_synthetic"] and done["llm"]["model"] == "fake-model"
        assert done["llm_usage"]["input_tokens"] > 0 and done["llm_usage"]["spent_usd_conservative"] > 0
        assert done["model_requests"] == calls and done["estimated_cost_usd"] == done["llm_usage"]["spent_usd_conservative"]
        trusted = system_prompt(EthicalFramework.GENERAL_PURPOSE)
        result = lab.repo.get(RESULT_KIND, done["experiment_id"])[0]
        secrets = [s for t in result["results"]["ground_truth"] for s in (t["scenario_id"], t["description"], t["rationale"])]
        for request in fake.calls:
            assert request["instructions"] == trusted and request["model"] == "fake-model" and request["store"] is False
            assert not any(s in request["input"] for s in secrets)
            assert '"label"' not in request["input"] and "ground_truth" not in request["input"]
        ev = load_evidence(lab.repo, done["experiment_id"], "wf_001")
        sent = {r["input"] for r in fake.calls}
        for cp in [c for c in ev["checkpoints"] if "llm_inputs" in c]:
            lo, gl = cp["llm_inputs"]["llm_local"], cp["llm_inputs"]["llm_global"]
            assert lo["context"]["observation"]["snapshot_digest"] == gl["context"]["observation"]["snapshot_digest"] == cp["snapshot_digest"]
            assert lo["context"]["observation"]["window"] == "LOCAL" and gl["context"]["observation"]["window"] == "GLOBAL"
            assert {e["event_id"] for e in lo["context"]["observation"]["events"]} <= {e["event_id"] for e in gl["context"]["observation"]["events"]}
            assert rq1_llm.exact_user_content(lo) in sent and rq1_llm.exact_user_content(gl) in sent
            assert cp["llm"]["llm_global"]["call"]["usage"]["available"]
        la = result["llm_analysis"]
        for cfg in rq1_llm.LLM_CONFIGS:
            m = la["primary"][cfg]
            assert m["tp"] + m["fp"] + m["tn"] + m["fn"] + m["unknown"] == len(PILOT)
    finally:
        db.close()


def test_failed_llm_calls_are_unknown_never_safe_and_make_the_experiment_inconclusive():
    db, lab = _lab(LIVE_ENV, FakeOpenAI(fail_every=3))
    try:
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="live", llm_checkpoints="all",
                                     confirm_paid_calls=True, llm_max_cost_usd=3.0, llm_max_requests=500)
        _, done = _run(lab, config)
        assert done["status"] == "INCONCLUSIVE" and any("unknown" in p for p in done["integrity_problems"])
        result = lab.repo.get(RESULT_KIND, done["experiment_id"])[0]
        la = result["llm_analysis"]
        assert la["failures"] and sum(la["unknown_workflow_predictions"].values()) > 0
        unknown = [r for r in la["rows"] if r["prediction"] == "UNKNOWN"]
        assert all(r["outcome"] == "UNKNOWN" and r["unknown_steps"] for r in unknown)
        for cfg in rq1_llm.LLM_CONFIGS:
            m = la["primary"][cfg]
            assert m["tp"] + m["fp"] + m["tn"] + m["fn"] == len(PILOT) - m["unknown"]
        failed = [r for r in result["checkpoint_rows"] if r["configuration"] in rq1_llm.LLM_CONFIGS and r["status"] == "FAILED"]
        assert failed and all(r["alert"] is None and r["error_kind"] for r in failed)
    finally:
        db.close()


def test_budget_exhaustion_leaves_later_calls_unattempted_and_unknown():
    db, lab = _lab(LIVE_ENV, FakeOpenAI())
    try:
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="live", llm_checkpoints="all",
                                     confirm_paid_calls=True, llm_max_cost_usd=3.0, llm_max_requests=500)
        validation = asyncio.run(lab.validate(config))
        tight = RQ1ExperimentConfig(**{**config.model_dump(), "llm_max_requests": 5})
        assert asyncio.run(lab.validate(tight))["status"] == "INVALID"  # the cap cannot cover the planned calls
        record = lab.prepare(tight, validation, "tester")  # stale validation: run-time re-validation must refuse
        done = asyncio.run(lab.run(record["experiment_id"]))
        assert done["status"] == "FAILED" and "validation failed at run time" in done["error"]
        assert done["model_requests"] == 0
    finally:
        db.close()


def test_server_cost_cap_and_request_cap_bound_every_experiment():
    db, lab = _lab({**LIVE_ENV, "SWARMGUARD_MAX_EVALUATION_COST_USD": "0.5"}, FakeOpenAI())
    try:
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="live", confirm_paid_calls=True,
                                     llm_max_cost_usd=2.0)
        validation = asyncio.run(lab.validate(config))
        budget = next(c for c in validation["checks"] if c["id"] == "llm_budget")
        assert validation["status"] == "INVALID" and "server cap" in budget["detail"]
        factory, _ = make_rq1_llm({**LIVE_ENV, "SWARMGUARD_MAX_LLM_REQUESTS": "7"}, client=FakeOpenAI())
        runtime = factory("live", 100, 1.0)
        assert runtime.settings["max_requests"] == 7 and runtime.settings["max_cost_usd"] == 1.0
        assert "api_key" not in json.dumps(runtime.settings) or runtime.settings["api_key_configured"] is True
        assert "sk-test" not in json.dumps(runtime.settings)
    finally:
        db.close()


def test_no_silent_mock_fallback_for_live_mode():
    factory, status = make_rq1_llm({})

    def lying_factory(mode, requests, cost):
        return factory("mock", requests, cost)  # a synthetic provider where a live one was requested
    db = Database()
    try:
        lab = RQ1Laboratory(db, llm_factory=lying_factory, llm_status=lambda: {**status(), "live": {**status()["mock"]}})
        config = RQ1ExperimentConfig(partition="development", scenario_ids=PILOT, llm_mode="live", confirm_paid_calls=True,
                                     llm_max_cost_usd=1.0)
        _, done = _run(lab, config)
        assert done["status"] == "FAILED" and "no silent mock fallback" in done["error"]
    finally:
        db.close()


def test_console_exposes_llm_records_to_researchers(tmp_path):
    factory, status = make_rq1_llm({})
    app = create_console_app(tmp_path / "c.db", local_access=True, step_delay=0, rq1_llm_factory=factory, rq1_llm_status=status)
    with TestClient(app, base_url="https://localhost") as client:
        assert client.get("/api/research/rq1").status_code == 401
        session = client.post("/api/session/local", json={}, headers=ORIGIN).json()
        headers = {**ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        overview = client.get("/api/research/rq1").json()
        assert overview["llm_status"]["mock"]["available"] and not overview["llm_status"]["live"]["available"]
        assert overview["recommended_config"]["llm_mode"] == "none"
        started = client.post("/api/research/rq1/experiments", headers=headers,
                              json={"partition": "development", "scenario_ids": PILOT, "llm_mode": "mock"})
        assert started.status_code == 202, started.text
        eid = started.json()["experiment_id"]
        for _ in range(300):
            record = client.get(f"/api/research/rq1/experiments/{eid}").json()
            if record["status"] in ("COMPLETED", "FAILED", "INCONCLUSIVE"):
                break
            time.sleep(0.2)
        assert record["status"] == "COMPLETED", record["error"]
        assert record["llm_usage"]["attempts"] > 0
        res = client.get(f"/api/research/rq1/experiments/{eid}/results").json()
        assert res["llm"]["is_synthetic"] and res["llm_analysis"]["primary"]["llm_global"] and res["metrics"]["llm"]["table"]
        raw = client.get(f"/api/research/rq1/experiments/{eid}/raw", params={"monitor": "LLM Global", "page_size": 200}).json()
        assert raw["total"] > 0 and {r["configuration"] for r in raw["rows"]} == {"llm_global"}
        row = raw["rows"][0]
        detail = client.get(f"/api/research/rq1/experiments/{eid}/raw/{row['workflow_id']}/{row['step']}/llm_global").json()
        assert detail["llm_prompt"]["is_synthetic"] and json.loads(detail["llm_prompt"]["user_content"])["proposed_action_id"]
        assert detail["monitor_input"]["window"] == "GLOBAL" and detail["ground_truth"]["label"] in ("SAFE", "UNSAFE")
        comparison = client.get(f"/api/research/rq1/experiments/{eid}/comparisons/{row['workflow_id']}").json()
        assert any(s["llm"] for s in comparison["steps"])
        failures = client.get(f"/api/research/rq1/experiments/{eid}/failures").json()
        assert failures["llm_counts"] and sum(failures["llm_counts"].values()) == len(PILOT)
        wf_rows = client.get(f"/api/research/rq1/experiments/{eid}/raw", params={"level": "workflow", "monitor": "LLM Local"}).json()
        assert wf_rows["total"] == len(PILOT)


def test_llm_modules_hold_no_network_code():
    import ast
    import inspect
    from swarmguard.research import rq1_lab
    for module in (rq1_llm, rq1_lab):
        tree = ast.parse(inspect.getsource(module))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(m.split(".")[0] in {"openai", "httpx", "requests", "socket", "smtplib", "swarmguard_providers"} for m in imported)
