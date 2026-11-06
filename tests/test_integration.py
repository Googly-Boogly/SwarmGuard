"""End-to-end acceptance, reproducibility, and no-network guarantees (spec items 37-40, 43)."""

from __future__ import annotations

import ast
import os
import socket
from pathlib import Path

import demo
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock
from swarmguard.ethics.council import build_mock_council
from swarmguard.evaluation.metrics import (
    ConfusionMatrix,
    false_positive_rate,
    precision,
    recall,
    true_positive_rate,
)
from swarmguard.evaluation.runner import ExperimentConfig, run_experiment
from swarmguard.evaluation.scenarios import DEMO_SCENARIOS, run_approval_laundering, run_authorized_email
from swarmguard.models.decisions import Decision, ReasonCode
from tests.conftest import run

PACKAGE = Path(__file__).resolve().parents[1] / "swarmguard"
FORBIDDEN_MODULES = {"socket", "smtplib", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib",
                     "telnetlib", "poplib", "imaplib", "subprocess", "openai", "anthropic"}


def test_37_approval_laundering_blocked(sim):
    o = run(run_approval_laundering(sim, "workflow_001"))
    r = o.final
    assert (r.tool, r.decision, r.reason_code, r.executed) == ("send_email", Decision.BLOCK,
                                                               ReasonCode.APPROVAL_MISMATCH, False)
    assert r.explanation == "The proposed operation differs from the human-approved operation."
    assert o.sent_email_count(sim) == 0
    assert [v.version for v in sim.workspace.versions(o.email_id)] == [1, 2]
    assert [s for s in o.steps if not s.executed] == [r]  # every other step was individually authorized


def test_38_authorized_email_succeeds(sim):
    o = run(run_authorized_email(sim, "workflow_002"))
    assert (o.final.decision, o.final.reason_code, o.final.executed) == (Decision.ALLOW, ReasonCode.AUTHORIZED, True)
    assert o.final.execution_result["status"] == "SIMULATED_EMAIL_SENT"
    (email,) = sim.sent_emails("workflow_002")
    assert (email["recipient"], email["resource_version"], email["approval_id"]) == ("customer@example.test", 1,
                                                                                    o.approval_id)


def _snapshot(path: Path) -> dict:
    clock = SteppingClock()
    sim = SwarmSimulator(path, clock=clock, council=build_mock_council(clock))
    outs = [run(run_approval_laundering(sim, "workflow_001")), run(run_authorized_email(sim, "workflow_002"))]
    snap = {
        "outcomes": [o.final.model_dump(mode="json") for o in outs],
        "events": [e.model_dump(mode="json") for wf in ("workflow_001", "workflow_002")
                   for e in sim.monitor.get_workflow_history(wf)],
        "decisions": [d.model_dump(mode="json") for wf in ("workflow_001", "workflow_002")
                      for d in sim.monitor.get_decisions(wf)],
        "graph": sim.graph.build_workflow_graph("workflow_001").model_dump(mode="json"),
        "emails": sim.sent_emails(),
    }
    sim.close()
    return snap


def test_39_demonstration_is_reproducible(tmp_path):
    assert _snapshot(tmp_path / "a.db") == _snapshot(tmp_path / "b.db")


def test_40_no_real_email_or_network(tmp_path, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    assert run(demo.main(tmp_path / "demo.db", True)) == 0
    out = capsys.readouterr().out
    assert "SWARMGUARD SECURITY EVENT" in out and "Unauthorized email execution prevented." in out
    assert "SWARMGUARD AUTHORIZED EXECUTION" in out and "Simulated emails sent: 0" in out
    assert "Simulated emails sent: 1" in out and "Acceptance check: PASS" in out


def test_package_contains_no_network_or_dynamic_execution():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "api.py":  # served by uvicorn; makes no outbound calls (checked below)
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in FORBIDDEN_MODULES]
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"eval", "exec", "compile", "__import__"}:
                offenders.append(f"{path.name}: {node.func.id}()")
    api_imports = {n.module.split(".")[0] for n in ast.walk(ast.parse((PACKAGE / "api.py").read_text()))
                   if isinstance(n, ast.ImportFrom) and n.module}
    assert not api_imports & FORBIDDEN_MODULES
    assert offenders == []


def test_43_runs_without_api_credentials(tmp_path, monkeypatch):
    for key in list(os.environ):
        if "API_KEY" in key or "TOKEN" in key or key.startswith(("OPENAI", "ANTHROPIC")):
            monkeypatch.delenv(key, raising=False)
    sim = SwarmSimulator(tmp_path / "nokeys.db", council=build_mock_council())
    assert run(run_authorized_email(sim, "wf")).final.executed
    sim.close()


def test_evaluation_interfaces_and_metrics():
    result = run(run_experiment(ExperimentConfig(experiment_id="smoke", use_mock_council=True)))
    assert result.is_research_result is False and "not evidence" in result.note
    assert [(p.scenario_id, p.flagged, p.matched_expected_decision) for p in result.predictions] == [
        ("approval_laundering", True, True), ("authorized_email", False, True)]
    assert result.metrics["tp"] == 1 and result.metrics["tn"] == 1
    assert [s.label.harmful for s in DEMO_SCENARIOS] == [True, False]
    empty = ConfusionMatrix()
    assert precision(empty) is None and recall(empty) is None and false_positive_rate(empty) is None
    m = ConfusionMatrix.from_pairs([(True, True), (True, False), (False, True), (False, False)])
    assert (precision(m), recall(m), true_positive_rate(m), false_positive_rate(m)) == (0.5, 0.5, 0.5, 0.5)
    try:
        run(run_experiment(ExperimentConfig(experiment_id="x", condition_id="specialized_ethics_x4")))
    except NotImplementedError:
        pass
    else:
        raise AssertionError("unimplemented condition must not silently run")
