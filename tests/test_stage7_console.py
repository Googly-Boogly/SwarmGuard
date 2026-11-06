"""Stage 7 console/control-service boundary: roles, separation of duties, confirmations, audit."""
from __future__ import annotations
import asyncio
import pytest
from fastapi.testclient import TestClient
from swarmguard.console.api import create_console_app
from swarmguard.console.auth import OperatorAccount, password_hash
from tests.stage7_support import APPROVER, WORKFLOW, Stage7Env

ORIGIN = {"Origin": "http://localhost:5173"}
PW = "isolated-stage7-password"
ROLES = {"admin_ada": {"ADMINISTRATOR"}, "auditor_aki": {"AUDITOR"}, "researcher_rin": {"RESEARCHER"},
         APPROVER: {"OPERATOR"}}


@pytest.fixture
def stack(tmp_path):
    env = Stage7Env(tmp_path)
    accounts = {u: OperatorAccount(u, password_hash(PW), frozenset(r)) for u, r in ROLES.items()}
    app = create_console_app(env.db_url, accounts=accounts, control_plane=env.control, checkpoint_dir=tmp_path / "cp",
                             step_delay=.001)
    with TestClient(app, base_url="https://localhost") as client:
        yield env, client


def login(client, username):
    client.cookies.clear()
    r = client.post("/api/session", json={"password": PW, "username": username}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json()["operator_id"] == username and set(r.json()["roles"]) == ROLES[username]
    return {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


CONFIRMED = {"reason": "incident drill", "confirm": True}


def test_reads_require_session_and_role(stack):
    env, client = stack
    assert client.get("/api/v1/security/status").status_code == 401
    login(client, "researcher_rin")
    assert client.get("/api/v1/security/status").status_code == 403
    assert client.get("/api/v1/security/identities").status_code == 403
    login(client, "auditor_aki")
    assert client.get("/api/v1/security/identities").status_code == 200
    report = client.get("/api/v1/audit/verification").json()
    assert report["status"] == "PASS" and report["chained_events"] > 0
    login(client, APPROVER)
    assert client.get("/api/v1/security/status").status_code == 200
    assert client.get("/api/v1/security/identities").status_code == 403


@pytest.mark.parametrize("user", ["auditor_aki", "researcher_rin", APPROVER])
def test_administrative_actions_require_administrator(stack, user):
    env, client = stack
    h = login(client, user)
    for path, body in (("/api/v1/security/emergency-stop", CONFIRMED),
                       ("/api/v1/security/identities/runtime_alpha/revoke", CONFIRMED),
                       ("/api/v1/security/capabilities/send_email", {**CONFIRMED, "enabled": False})):
        assert client.post(path, json=body, headers=h).status_code == 403
    assert env.control.emergency_state()["active"] is False


def test_emergency_controls_need_confirmation_and_explicit_recovery(stack):
    env, client = stack
    h = login(client, "admin_ada")
    assert client.post("/api/v1/security/emergency-stop", json={"reason": "drill"}, headers=h).status_code == 422
    assert client.post("/api/v1/security/emergency-stop", json={"reason": "drill", "confirm": False},
                       headers=h).status_code == 422
    r = client.post("/api/v1/security/emergency-stop", json=CONFIRMED, headers=h)
    assert r.status_code == 200 and r.json()["active"] and r.json()["activated_by"] == "admin_ada"
    blocked = asyncio.run(env.call(env.token(), request_id="es", workflow_id=WORKFLOW, tool="read_document",
                                   resource_id="doc_service_faq")).json()
    assert blocked["reason_code"] == "EMERGENCY_STOP_ACTIVE"
    bad = client.post("/api/v1/security/emergency-stop/recover", json={"reason": "ok", "confirmation_phrase": "yes"},
                      headers=h)
    assert bad.status_code == 422 and env.control.emergency_state()["active"]
    ok = client.post("/api/v1/security/emergency-stop/recover",
                     json={"reason": "drill complete", "confirmation_phrase": "RESUME PROTECTED OPERATIONS"}, headers=h)
    assert ok.status_code == 200 and not ok.json()["active"]


def test_browser_cannot_assert_identity_or_role(stack):
    env, client = stack
    h = login(client, "admin_ada")
    for body in ({**CONFIRMED, "operator_id": "root"}, {**CONFIRMED, "role": "ADMINISTRATOR"}, {**CONFIRMED, "by": "x"}):
        assert client.post("/api/v1/security/emergency-stop", json=body, headers=h).status_code == 422
    assert client.post("/api/session", json={"password": PW, "username": "admin_ada", "roles": ["X"]},
                       headers=ORIGIN).status_code == 422


def test_csrf_origin_and_agent_tokens_are_refused(stack):
    env, client = stack
    h = login(client, "admin_ada")
    for headers in (ORIGIN, {**h, "X-CSRF-Token": "bad"}, {**h, "Origin": "https://evil.example"}):
        assert client.post("/api/v1/security/emergency-stop", json=CONFIRMED, headers=headers).status_code == 403
    client.cookies.clear()
    agent = {**ORIGIN, "Authorization": f"Bearer {env.token()}"}
    for path in ("/api/v1/security/emergency-stop", "/api/v1/approvals/approval_001/revoke"):
        assert client.post(path, json=CONFIRMED, headers=agent).status_code == 401
    assert client.get("/api/v1/security/status", headers=agent).status_code == 401


def test_identity_revocation_takes_effect_at_gateway(stack):
    env, client = stack
    token = env.token()
    h = login(client, "admin_ada")
    r = client.post("/api/v1/security/identities/runtime_alpha/revoke", json=CONFIRMED, headers=h)
    assert r.status_code == 200 and r.json()["kind"] == "runtime"
    res = asyncio.run(env.call(token, request_id="after", workflow_id=WORKFLOW, tool="read_document",
                               resource_id="doc_service_faq"))
    assert res.status_code == 401
    identities = client.get("/api/v1/security/identities", headers=h).json()
    assert next(r for r in identities["runtimes"] if r["runtime_id"] == "runtime_alpha")["status"] == "REVOKED"


def test_operator_approves_as_named_approver_and_can_revoke(stack):
    env, client = stack
    tok = env.token()
    _, aid = asyncio.run(env.draft_and_request(tok, "c"))
    queue = {a["approval_id"]: a for a in client.get("/api/approvals").json()}
    body = {"decision": "APPROVE", "operation_hash": queue[aid]["operation_hash"], "confirmed": True}
    h = login(client, "admin_ada")
    assert client.post(f"/api/approvals/{aid}", json=body, headers=h).status_code == 403   # admin is not an approver
    h = login(client, APPROVER)
    r = client.post(f"/api/approvals/{aid}", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert env.control.approvals.get(aid).approver_id == APPROVER
    revoke = client.post(f"/api/v1/approvals/{aid}/revoke", json=CONFIRMED, headers=h)
    assert revoke.status_code == 200 and revoke.json()["status"] == "REVOKED"


def test_failed_logins_and_operator_actions_are_audited_and_rate_limited(stack):
    env, client = stack
    for _ in range(5):
        assert client.post("/api/session", json={"password": "wrong-password", "username": "admin_ada"},
                           headers=ORIGIN).status_code == 401
    assert client.post("/api/session", json={"password": PW, "username": "admin_ada"}, headers=ORIGIN).status_code == 429
    kinds = [e.event_type.value for e in env.control.repos.events.query(workflow_id="system_security")]
    assert kinds.count("AUTHENTICATION_FAILED") >= 5


def test_service_health_operations_and_checkpoints(stack):
    env, client = stack
    asyncio.run(env.call(env.token(), request_id="h1", workflow_id=WORKFLOW, tool="read_document",
                         resource_id="doc_service_faq"))
    h = login(client, "auditor_aki")
    services = client.get("/api/v1/services/health").json()["services"]
    assert any(s["service"] == "control" and s["readiness"]["migrations"] == "ok" for s in services)
    ops = client.get("/api/v1/security/operations").json()
    detail = client.get(f"/api/v1/security/operations/{ops[0]['operation_id']}").json()
    assert detail["state"] == "SUCCEEDED" and [t["to_state"] for t in detail["transitions"]][:2] == ["PENDING", "AUTHORIZED"]
    assert detail["decision"]["decision"] == "ALLOW" and detail["events"]
    assert client.post("/api/v1/audit/checkpoints", json=CONFIRMED, headers=h).status_code == 201
    assert len(client.get("/api/v1/audit/checkpoints").json()) == 1
    assert client.get("/api/v1/audit/verification").json()["checkpoints_checked"] == 1


def test_legacy_single_password_account_keeps_researcher_and_operator_only(tmp_path):
    app = create_console_app(tmp_path / "legacy.db", encoded_password=password_hash(PW), step_delay=.001)
    with TestClient(app, base_url="https://localhost") as client:
        r = client.post("/api/session", json={"password": PW}, headers=ORIGIN)
        assert set(r.json()["roles"]) == {"RESEARCHER", "OPERATOR"}
        h = {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}
        assert client.post("/api/v1/security/emergency-stop", json=CONFIRMED, headers=h).status_code == 403
        assert client.get("/api/v1/security/status").status_code == 503   # no control plane configured: no fallback


def test_control_plane_approvals_are_actionable_in_the_queue(stack):
    env, client = stack
    _, aid = asyncio.run(env.draft_and_request(env.token(), "q"))
    entry = next(a for a in client.get("/api/approvals").json() if a["approval_id"] == aid)
    assert entry["actionable"] is True and entry["status"] == "PENDING"
    env.approve(aid)
    entry = next(a for a in client.get("/api/approvals").json() if a["approval_id"] == aid)
    assert entry["actionable"] is False   # granted requests are no longer actionable
