"""Read-only FastAPI observability (spec items 41-42)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.api import create_app
from swarmguard.evaluation.scenarios import run_approval_laundering, run_authorized_email
from swarmguard.storage.database import Database
from tests.conftest import run


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "api.db"
    sim = SwarmSimulator(path)
    bad = run(run_approval_laundering(sim, "workflow_001"))
    run(run_authorized_email(sim, "workflow_002"))
    sim.close()
    with TestClient(create_app(path)) as c:
        c.bad = bad  # type: ignore[attr-defined]
        yield c


def test_41_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["read_only"] is True and body["api_key_required"] is False


def test_42_endpoints_return_persisted_records(client):
    assert [w["workflow_id"] for w in client.get("/workflows").json()] == ["workflow_001", "workflow_002"]
    assert client.get("/workflows/workflow_001").json()["status"] == "ACTIVE"
    events = client.get("/workflows/workflow_001/events").json()
    assert events[0]["event_type"] == "WORKFLOW_STARTED" and any(e["event_type"] == "SECURITY_ALERT" for e in events)
    decisions = client.get("/workflows/workflow_001/decisions").json()
    assert decisions[-1]["reason_code"] == "APPROVAL_MISMATCH" and decisions[-1]["executed"] is False
    graph = client.get("/workflows/workflow_001/graph").json()
    assert graph["nodes"] and any(e["edge_type"] == "INVALIDATED_BY" for e in graph["edges"])
    summary = client.get("/workflows/workflow_001/summary").json()
    assert summary["simulated_emails_sent"] == 0 and summary["blocked_actions"][0]["reason_code"] == "APPROVAL_MISMATCH"
    assert client.get("/workflows/workflow_002/summary").json()["simulated_emails_sent"] == 1
    deps = client.get(f"/actions/{client.bad.final.action_id}/dependencies").json()
    assert deps["approvals"] == [client.bad.approval_id]


def test_unknown_workflows_404_and_input_validated(client):
    for path in ("/workflows/nope", "/workflows/nope/events", "/workflows/nope/decisions", "/workflows/nope/graph",
                 "/workflows/nope/summary", "/actions/action_999/dependencies"):
        assert client.get(path).status_code == 404, path
    assert client.get("/workflows/x';DROP TABLE events;--/events").status_code in (404, 422)
    assert client.get("/workflows/workflow_001/events").status_code == 200  # tables intact


def test_no_mutating_or_privileged_endpoints(client):
    for route in client.app.routes:
        methods = set(getattr(route, "methods", set()) or set())
        assert methods <= {"GET", "HEAD"}, route.path
        assert not any(w in route.path for w in ("approve", "execute", "policy", "identity", "agent"))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/workflows/workflow_001/events").status_code == 405


def test_api_database_handle_is_read_only(tmp_path):
    path = tmp_path / "ro.db"
    Database(path).close()
    db = Database(path, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        db.execute("INSERT INTO counters(name, value) VALUES ('x', 1)")
    db.close()
