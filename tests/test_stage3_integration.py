"""Stage 3 integration (spec items 49-54)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import demo
import demo_stage2
import demo_stage3
import run_ethics_evaluation
from swarmguard.api import create_app
from tests.conftest import run


def test_49_50_stage1_and_stage2_demonstrations_still_work(tmp_path, capsys):
    assert run(demo.main(tmp_path / "s1.db", True)) == 0
    assert run(demo_stage2.main(tmp_path / "s2.db")) == 0
    out = capsys.readouterr().out
    assert "Acceptance check: PASS" in out and "Stage 2 demonstration check: PASS" in out


def test_51_stage3_mock_demonstration_completes(tmp_path, capsys):
    assert run(demo_stage3.main("mock", tmp_path / "s3.db")) == 0
    out = capsys.readouterr().out
    for heading in ("VIRTUE ETHICS", "CONSEQUENTIALISM", "DEONTOLOGY", "CARE ETHICS", "COUNCIL SUMMARY",
                    "DETERMINISTIC AUTHORIZATION", "Stage 3 demonstration check: PASS", "SYNTHETIC"):
        assert heading in out
    assert run(demo_stage3.main("mock", tmp_path / "s3b.db", "deontology")) == 0
    assert "Failed Monitors:\n1 (llm_deontology)" in capsys.readouterr().out


def test_live_demo_refuses_without_configuration(tmp_path, monkeypatch, capsys):
    # Hermetic: a developer's .env (key, model, prices) must not turn this into a configured live run.
    for module in (demo_stage3, run_ethics_evaluation):
        monkeypatch.setattr(module, "load_env_file", lambda *a, **k: False)
    for key in ("OPENAI_API_KEY", "SWARMGUARD_OPENAI_MODEL", "SWARMGUARD_PRICE_INPUT_USD_PER_MTOK",
                "SWARMGUARD_PRICE_OUTPUT_USD_PER_MTOK", "SWARMGUARD_LLM_MAX_REQUESTS"):
        monkeypatch.delenv(key, raising=False)
    assert run(demo_stage3.main("openai", tmp_path / "x.db")) == 2
    assert run(run_ethics_evaluation.main("openai", tmp_path, "x", 0, None, "critical", "MAJORITY_WITH_ESCALATION",
                                          None)) == 2
    assert "no request was made" in capsys.readouterr().err
    assert not (tmp_path / "x.db").exists()


def test_52_stage3_mock_evaluation_completes(tmp_path, capsys):
    rc = run(run_ethics_evaluation.main("mock", tmp_path, "eval_cli", 0, ["distributed_approval_001",
                                                                          "legitimate_collaboration_001"],
                                        "critical", "MAJORITY_WITH_ESCALATION", None))
    assert rc == 0 and (tmp_path / "eval_cli" / "metrics.json").is_file()
    assert "SYNTHETIC MOCK" in capsys.readouterr().out
    assert run(run_ethics_evaluation.main("mock", tmp_path, "eval_cli", 0, None, "critical",
                                          "MAJORITY_WITH_ESCALATION", None)) == 2  # no silent overwrite


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "api3.db"
    assert run(demo_stage3.main("mock", db)) == 0
    assert run(run_ethics_evaluation.main("mock", tmp_path / "results" / "stage3", "api_eth", 0,
                                          ["legitimate_collaboration_001"], "critical", "MAJORITY_WITH_ESCALATION",
                                          None)) == 0
    with TestClient(create_app(db, results_dir=tmp_path / "results")) as c:
        yield c


def test_53_54_existing_and_new_endpoints(client, capsys):
    capsys.readouterr()
    assert client.get("/health").status_code == 200 and client.get("/workflows").json()
    assert client.get("/workflows/wf_demo_001/risk").json()["recommendation"] in ("ALLOW", "ESCALATE", "BLOCK")
    ethics = client.get("/workflows/wf_demo_001/ethics").json()
    assert len(ethics) == 1 and ethics[0]["advisory"] is True and ethics[0]["council_type"] == "SPECIALIZED_ETHICS"
    assessed = client.get("/workflows/wf_demo_001/ethics/assessments").json()
    assert {a["framework"] for a in assessed[0]["assessments"]} == {"virtue_ethics", "consequentialism",
                                                                     "deontology", "care_ethics"}
    dis = client.get("/workflows/wf_demo_001/ethics/disagreement").json()
    assert dis[0]["disagreement"]["n_assessed"] == 4
    listing = client.get("/evaluations/ethics").json()
    assert [e["experiment_id"] for e in listing] == ["api_eth"] and listing[0]["provider_is_synthetic"] is True
    detail = client.get("/evaluations/ethics/api_eth").json()
    assert set(detail) >= {"experiment_config", "metrics", "monitor_assessments", "council_results"}
    assert client.get("/evaluations/ethics/missing").status_code == 404
    assert client.get("/evaluations/ethics/..%2F..%2Fetc").status_code in (404, 422)
    assert client.get("/evaluations").status_code == 200  # Stage 2 listing unaffected
    for method in ("post", "put", "delete"):
        assert getattr(client, method)("/workflows/wf_demo_001/ethics").status_code == 405
