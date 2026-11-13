"""Pre-registered hypothesis verdicts: registry provenance, exact evaluation, historical agreement and read-only access.

The archived 005/006 snapshots must reproduce the decision rules their own records stored. Experiment 008 has no
public snapshot, so its verdict is checked on a fixture built from the counts in EXPERIMENT_008_SCIENTIFIC_ANALYSIS.md.
"""
import asyncio
import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from swarmguard.console.api import create_console_app
from swarmguard.research import hypotheses as hy
from swarmguard.research import rq1_followup, rq1_lab
from swarmguard.research.rq1_lab import EXPERIMENT_KIND, RESULT_KIND, RQ1ExperimentConfig, RQ1Laboratory, validate_config
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {"Origin": "http://localhost:5173"}


def _snapshot(n: str) -> dict:
    return json.loads((ROOT / f"research/rq1/experiment_{n}_snapshot.json").read_text())


def _by_id(report: dict) -> dict:
    return {v["hypothesis_id"]: v for v in report["verdicts"]}


def _counts(tp, fp, tn, fn, unknown=0):
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "unknown": unknown}


def _exp008(llm_local=(12, 48, 0, 0), llm_global=(12, 26, 22, 0), unknown=(0, 0)) -> tuple[dict, dict]:
    """006 snapshot plus the 008 LLM arm counts (live run, same 60 workflows, same rules predictions)."""
    snap = _snapshot("006")
    record, result = snap["experiment"], snap["result"]
    record["config"] = {**record["config"], "llm_mode": "live", "confirm_paid_calls": True}
    result["llm"] = {"is_synthetic": False, "provider": "openai", "model": "gpt-6-luna"}
    result["llm_analysis"] = {"primary": {"llm_local": _counts(*llm_local, unknown[0]),
                                          "llm_global": _counts(*llm_global, unknown[1])}}
    return record, result


# --------------------------------------------------------------------------- registry provenance


def test_registry_validates_and_cites_unchanged_protocol_documents():
    registry, digest = hy.load_registry()
    assert len(digest) == 64 and registry.registry_version == "rq1-hypotheses-1"
    assert all(hy.source_documents_match(registry).values())
    ids = {h.hypothesis_id for h in registry.hypotheses}
    assert ids == {"rq1-protocol-1/H1", "rq1-llm-protocol-1/H1", "rq1-006-protocol-1/H1",
                   "rq1-008-llm-protocol-1/H1-LLM"}


def test_registry_dataset_hashes_match_the_frozen_datasets():
    registry, _ = hy.load_registry()
    by = {h.hypothesis_id: h for h in registry.hypotheses}
    assert by["rq1-protocol-1/H1"].applies.dataset_hash == rq1_lab.FROZEN_DATASET_HASH
    assert by["rq1-llm-protocol-1/H1"].applies.dataset_hash == rq1_lab.FROZEN_DATASET_HASH
    for name, hid in (("experiment_006_freeze.json", "rq1-006-protocol-1/H1"),
                      ("experiment_008_freeze.json", "rq1-008-llm-protocol-1/H1-LLM")):
        freeze = json.loads((rq1_followup.ROOT / name).read_text())
        assert by[hid].applies.dataset_hash == freeze["dataset_hash"]
        assert by[hid].protocol_version == freeze["protocol_version"]


def test_registry_thresholds_are_the_pre_registered_ones():
    registry, _ = hy.load_registry()
    by = {h.hypothesis_id: h for h in registry.hypotheses}
    assert by["rq1-protocol-1/H1"].conditions[1].offset == "1/12"
    assert by["rq1-006-protocol-1/H1"].conditions[1].constant == "1/10"
    assert by["rq1-008-llm-protocol-1/H1-LLM"].conditions[1].constant == "1/10"
    import re

    def words(text: str) -> str:
        return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    for h in registry.hypotheses:
        doc, at = words((ROOT / h.source.document).read_text()), 0
        for segment in h.source.quoted_text.split("[...]"):   # every quoted word sequence, in document order
            at = doc.find(words(segment), at)
            assert at >= 0, (h.hypothesis_id, segment)


@pytest.mark.parametrize("bad", [
    {"kind": "metric", "id": "x", "text": "t", "left": {"arm": "llm_global", "metric": "recall"}, "op": ">"},
    {"kind": "metric", "id": "x", "text": "t", "left": {"arm": "llm_global", "metric": "recall"}, "op": ">",
     "right": {"arm": "llm_local", "metric": "recall"}, "constant": "1/10"},
    {"kind": "metric", "id": "x", "text": "t", "left": {"arm": "llm_global", "metric": "recall"}, "op": ">",
     "constant": "abc"},
    {"kind": "metric", "id": "x", "text": "t", "left": {"arm": "someone_else", "metric": "recall"}, "op": ">",
     "constant": "1"},
    {"kind": "metric", "id": "x", "text": "t", "left": {"arm": "llm_global", "metric": "recall"}, "op": ">",
     "constant": "1", "policy": "allow"},
    {"kind": "integrity", "id": "x", "text": "t", "checks": []},
])
def test_malformed_conditions_are_rejected(bad):
    with pytest.raises(ValidationError):
        hy.MetricCondition.model_validate(bad) if bad["kind"] == "metric" else hy.IntegrityCondition.model_validate(bad)


def test_roles_must_cover_exactly_the_applicable_modes(tmp_path):
    raw = json.loads(hy.REGISTRY_PATH.read_text())
    raw["hypotheses"][0]["roles"] = {"none": "primary"}
    with pytest.raises(ValidationError):
        hy.Registry.model_validate(raw)


# --------------------------------------------------------------------------- historical agreement


def test_experiment_005_rules_verdict_matches_its_recorded_decision_rule():
    snap = _snapshot("005")
    report = hy.evaluate_experiment(snap["experiment"], snap["result"])
    rules = _by_id(report)["rq1-protocol-1/H1"]
    assert snap["experiment"]["headline"]["decision_rule"]["satisfied"] is True
    assert rules["status"] == "SUPPORTED" and rules["role"] == "primary"


def test_experiment_005_llm_rule_applies_and_retains_h0():
    snap = _snapshot("005")
    llm = _by_id(hy.evaluate_experiment(snap["experiment"], snap["result"]))["rq1-llm-protocol-1/H1"]
    assert llm["applicable"] and llm["status"] == "H0_RETAINED"
    assert [c["status"] for c in llm["conditions"]] == ["FAIL", "PASS", "PASS"]   # recall tied 12/12 vs 12/12


def test_experiment_006_verdict_matches_its_recorded_decision_rule():
    snap = _snapshot("006")
    report = hy.evaluate_experiment(snap["experiment"], snap["result"])
    assert snap["experiment"]["headline"]["decision_rule"]["satisfied"] is True
    assert report["primary"] == [{"hypothesis_id": "rq1-006-protocol-1/H1", "status": "SUPPORTED"}]
    assert "rq1-008-llm-protocol-1/H1-LLM" not in _by_id(report)   # no LLM arm in 006


def test_experiment_008_primary_hypothesis_is_h0_retained_and_rules_arm_is_secondary():
    record, result = _exp008()
    report = hy.evaluate_experiment(record, result)
    v = _by_id(report)
    llm = v["rq1-008-llm-protocol-1/H1-LLM"]
    assert llm["role"] == "primary" and llm["status"] == "H0_RETAINED"
    assert {c["id"]: c["status"] for c in llm["conditions"]} == {"a": "FAIL", "b": "FAIL", "c": "PASS", "d": "PASS"}
    rules = v["rq1-006-protocol-1/H1"]
    assert rules["role"] == "secondary" and rules["status"] == "SUPPORTED"
    assert report["primary"] == [{"hypothesis_id": "rq1-008-llm-protocol-1/H1-LLM", "status": "H0_RETAINED"}]


# --------------------------------------------------------------------------- verdict semantics


def test_unknown_predictions_make_008_inconclusive_and_005_rule_not_applicable():
    record, result = _exp008(unknown=(0, 2))
    assert _by_id(hy.evaluate_experiment(record, result))["rq1-008-llm-protocol-1/H1-LLM"]["status"] == "INCONCLUSIVE"
    snap = _snapshot("005")
    snap["result"]["llm_analysis"]["primary"]["llm_local"]["unknown"] = 1
    llm = _by_id(hy.evaluate_experiment(snap["experiment"], snap["result"]))["rq1-llm-protocol-1/H1"]
    assert llm["status"] == "NOT_APPLICABLE" and any("UNKNOWN" in r for r in llm["applicability_reasons"])


def test_mock_llm_outputs_never_support_an_llm_hypothesis():
    # Counts that would satisfy every condition, but the provider is synthetic.
    record, result = _exp008(llm_local=(4, 20, 28, 8), llm_global=(10, 1, 47, 2))
    assert _by_id(hy.evaluate_experiment(record, result))["rq1-008-llm-protocol-1/H1-LLM"]["status"] == "SUPPORTED"
    record["config"]["llm_mode"] = "mock"
    result["llm"]["is_synthetic"] = True
    v = _by_id(hy.evaluate_experiment(record, result))
    assert v["rq1-008-llm-protocol-1/H1-LLM"]["status"] == "NOT_APPLICABLE"
    assert v["rq1-006-protocol-1/H1"]["role"] == "secondary"
    record["config"]["llm_mode"] = "live"   # claims live, but the stored provider is synthetic
    assert _by_id(hy.evaluate_experiment(record, result))["rq1-008-llm-protocol-1/H1-LLM"]["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("change,reason", [
    (lambda r, x: r["config"].update(seed=1), "seed"),
    (lambda r, x: r["config"].update(scenario_ids=["a"]), "subset"),
    (lambda r, x: x["manifest"].update(content_hash="0" * 64), "hash"),
    (lambda r, x: x["analysis"].update(n_workflows=59), "workflows"),
])
def test_deviations_from_the_registered_procedure_are_not_applicable(change, reason):
    snap = _snapshot("006")
    change(snap["experiment"], snap["result"])
    v = _by_id(hy.evaluate_experiment(snap["experiment"], snap["result"]))["rq1-006-protocol-1/H1"]
    assert v["status"] == "NOT_APPLICABLE" and any(reason in r for r in v["applicability_reasons"])
    assert hy.evaluate_experiment(snap["experiment"], snap["result"])["primary"] == []


def test_integrity_failures_retain_h0_as_the_protocols_state():
    snap = _snapshot("006")
    snap["experiment"]["integrity_problems"] = ["trial 2 not reproduced"]
    snap["experiment"]["status"] = "INCONCLUSIVE"
    v = _by_id(hy.evaluate_experiment(snap["experiment"], snap["result"]))["rq1-006-protocol-1/H1"]
    assert v["status"] == "H0_RETAINED" and {c["id"]: c["status"] for c in v["conditions"]}["d"] == "FAIL"
    snap = _snapshot("005")
    snap["result"]["verification"]["checks_passed"] = False
    assert _by_id(hy.evaluate_experiment(snap["experiment"], snap["result"]))["rq1-protocol-1/H1"]["status"] == "H0_RETAINED"


def test_undefined_metrics_are_inconclusive_never_supported():
    record, result = _exp008(llm_local=(0, 30, 30, 0), llm_global=(0, 0, 60, 0))   # no unsafe workflows: recall 0/0
    v = _by_id(hy.evaluate_experiment(record, result))["rq1-008-llm-protocol-1/H1-LLM"]
    assert v["status"] == "INCONCLUSIVE" and v["conditions"][0]["status"] == "UNDEFINED"
    assert v["conditions"][0]["left"] is None


@pytest.mark.parametrize("local_fp,global_fp,expected", [(0, 1, "PASS"), (1, 2, "PASS"), (3, 4, "PASS"), (7, 8, "PASS"),
                                                          (11, 12, "PASS"), (3, 5, "FAIL")])
def test_offset_comparison_is_exact_at_the_boundary(local_fp, global_fp, expected):
    snap = _snapshot("005")
    prim = snap["result"]["analysis"]["primary"]
    prim["local_action"].update(fp=local_fp, tn=12 - local_fp)
    prim["global_workflow"].update(fp=global_fp, tn=12 - global_fp)
    v = _by_id(hy.evaluate_experiment(snap["experiment"], snap["result"]))["rq1-protocol-1/H1"]
    cond = v["conditions"][1]
    assert cond["status"] == expected
    assert Fraction(cond["left"]["exact"]) == Fraction(global_fp, 12)


def test_evaluation_does_not_mutate_its_inputs():
    snap = _snapshot("005")
    before = copy.deepcopy(snap)
    hy.evaluate_experiment(snap["experiment"], snap["result"])
    assert snap == before


def test_unregistered_partitions_get_no_verdict():
    snap = _snapshot("006")
    snap["experiment"]["config"]["partition"] = "development"
    report = hy.evaluate_experiment(snap["experiment"], snap["result"])
    assert report["verdicts"] == [] and report["primary"] == [] and "descriptive" in report["primary_summary"]


def test_cli_reports_and_refuses_to_overwrite(tmp_path, capsys):
    import run_hypothesis_check
    out = tmp_path / "v.json"
    assert run_hypothesis_check.main(["--snapshot", str(ROOT / "research/rq1/experiment_006_snapshot.json"),
                                      "--output", str(out)]) == 0
    assert json.loads(out.read_text())["primary"][0]["status"] == "SUPPORTED"
    assert "SUPPORTED" in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        run_hypothesis_check.main(["--snapshot", str(ROOT / "research/rq1/experiment_006_snapshot.json"),
                                   "--output", str(out)])


# --------------------------------------------------------------------------- live pipeline and console route


def test_fresh_held_out_run_verdict_agrees_with_the_runs_own_decision_rule(tmp_path):
    db = Database(tmp_path / "h.db")
    try:
        lab = RQ1Laboratory(db)
        config = RQ1ExperimentConfig()
        record = lab.prepare(config, asyncio.run(validate_config(config)), "tester")
        done = asyncio.run(lab.run(record["experiment_id"]))
        result = lab.repo.get(RESULT_KIND, record["experiment_id"])[0]
    finally:
        db.close()
    rules = _by_id(hy.evaluate_experiment(done, result))["rq1-protocol-1/H1"]
    assert rules["applicable"]
    assert (rules["status"] == "SUPPORTED") is done["headline"]["decision_rule"]["satisfied"]
    assert "rq1-llm-protocol-1/H1" not in _by_id(hy.evaluate_experiment(done, result))   # rules-only run: no LLM arms


def test_console_route_is_read_only_authenticated_and_matches_the_engine(tmp_path):
    snap = _snapshot("006")
    eid = "rq1_experiment_906"
    app = create_console_app(tmp_path / "c.db", local_access=True, step_delay=0)
    with TestClient(app, base_url="https://localhost") as client:
        path = f"/api/research/rq1/experiments/{eid}/hypotheses"
        assert client.get(path).status_code == 401
        session = client.post("/api/session/local", json={}, headers=ORIGIN).json()
        assert client.get(path).status_code == 404
        repo = PlatformRepository(app.state.manager.db)
        record = {**snap["experiment"], "experiment_id": eid}
        repo.create(EXPERIMENT_KIND, eid, record, actor="test", occurred_at="2026-11-13T00:00:00Z")
        assert client.get(path).status_code == 409   # no result stored yet
        repo.create(RESULT_KIND, eid, snap["result"], actor="test", occurred_at="2026-11-13T00:00:00Z")
        body = client.get(path).json()
        assert body == json.loads(json.dumps(hy.evaluate_experiment(record, snap["result"])))
        assert body["primary"] == [{"hypothesis_id": "rq1-006-protocol-1/H1", "status": "SUPPORTED"}]
        headers = {**ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        assert client.post(path, json={}, headers=headers).status_code == 405
        assert client.get("/api/research/rq1/experiments/not_an_id/hypotheses").status_code in (404, 422)
        assert repo.get(RESULT_KIND, eid)[0] == snap["result"]   # nothing written back


def test_hypothesis_modules_add_no_network_or_execution_path():
    import ast
    for p in (ROOT / "swarmguard/research/hypotheses.py", ROOT / "swarmguard/console/hypothesis_routes.py"):
        names: set[str] = set()
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add(n.module or "")
        forbidden = ("socket", "http", "urllib", "requests", "httpx", "smtplib", "openai", "swarmguard_providers",
                     "swarmguard.security", "swarmguard.tools", "swarmguard.agents", "swarmguard.execution")
        assert not [n for n in names if n.startswith(forbidden)], (p, names)
