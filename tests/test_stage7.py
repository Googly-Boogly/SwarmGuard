"""Stage 7: service identity, remote gateway, isolated execution, idempotency, concurrency, failure
injection, operator controls and audit integrity. Isolated SQLite files; no network (ASGI transports)."""
from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
import httpx
import jwt
import pytest
from swarmguard.audit.hash_chain import link_hash, payload_hash
from swarmguard.audit.verification import verify_chain, write_checkpoint, load_checkpoints
from swarmguard.authentication.keys import generate_keypair
from swarmguard.authentication.service_identity import GATEWAY_AUDIENCE, ISSUER
from swarmguard.execution.grants import issue_grant
from swarmguard.infrastructure.failure_recovery import recover_gateway
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.security.decision_engine import DecisionEngine
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ProtectedOperationRepository
from cryptography.hazmat.primitives import serialization
from tests.stage7_support import APPROVER, EMAIL, WORKFLOW, Stage7Env


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    return Stage7Env(tmp_path)


def other_private_key():
    return serialization.load_pem_private_key(generate_keypair()[0], None)


def read_body(rid="read_1", **kw):
    return {"request_id": rid, "workflow_id": WORKFLOW, "tool": "read_document", "resource_id": "doc_service_faq", **kw}


def sent_count(env):
    return len(env.control.db.fetchall("SELECT * FROM sent_emails"))


async def approved_send(env, prefix="a"):
    tok = env.token()
    eid, aid = await env.draft_and_request(tok, prefix)
    env.approve(aid)
    return tok, eid, aid


# ----------------------------------------------------------------------------------------- authentication

def test_valid_service_identity_authenticates_and_resolves_role_server_side(env):
    r = run(env.call(env.token(), **read_body()))
    assert r.status_code == 200 and r.json()["decision"] == "ALLOW" and r.json()["executed"]
    assert r.json()["execution_state"] == "SUCCEEDED"
    decision = env.control.monitor.get_decision_for_action(r.json()["action_id"])
    assert decision.agent_id == "operations_agent"


@pytest.mark.parametrize("case", ["missing", "garbage", "foreign_key", "expired", "wrong_audience", "hs256", "none_alg",
                                  "too_long_lifetime", "wrong_type"])
def test_invalid_credentials_are_rejected_and_audited(env, case):
    now = int(datetime.now(UTC).timestamp())
    claims = {"iss": ISSUER, "aud": GATEWAY_AUDIENCE, "sub": "operations_agent", "rt": "runtime_alpha", "jti": "cred_x",
              "iat": now, "nbf": now, "exp": now + 300, "typ": "agent-runtime"}
    token = {
        "missing": None, "garbage": "not-a-jwt",
        "foreign_key": jwt.encode(claims, other_private_key(), algorithm="EdDSA"),
        "expired": jwt.encode({**claims, "iat": now - 900, "nbf": now - 900, "exp": now - 600}, env.identity_private,
                              algorithm="EdDSA"),
        "wrong_audience": jwt.encode({**claims, "aud": "swarmguard-executor"}, env.identity_private, algorithm="EdDSA"),
        "hs256": jwt.encode(claims, "x" * 32, algorithm="HS256"),
        "none_alg": jwt.encode(claims, None, algorithm="none"),
        "too_long_lifetime": jwt.encode({**claims, "exp": now + 3 * 86400}, env.identity_private, algorithm="EdDSA"),
        "wrong_type": jwt.encode({**claims, "typ": "operator"}, env.identity_private, algorithm="EdDSA"),
    }[case]
    r = run(env.call(token, **read_body()))
    assert r.status_code == 401 and r.json()["decision"] == "BLOCK" and not r.json()["executed"]
    failures = [e for e in env.control.repos.events.query(workflow_id="system_security")
                if e.event_type.value == "AUTHENTICATION_FAILED"]
    assert failures, "authentication failure was not audited"
    blocks = env.control.repos.decisions.for_workflow(WORKFLOW)
    assert blocks and blocks[-1].reason_code.value in ("AUTHENTICATION_FAILED", "CREDENTIAL_REVOKED")


@pytest.mark.parametrize("target", ["credential", "runtime", "agent"])
def test_revoked_identities_are_rejected(env, target):
    token, jti = env.issuer.issue(agent_id="operations_agent", runtime_id="runtime_alpha")
    assert run(env.call(token, **read_body("before"))).status_code == 200
    identity = {"credential": jti, "runtime": "runtime_alpha", "agent": "operations_agent"}[target]
    env.control.revoke_identity(identity, by="admin_test", reason="suspected compromise")
    r = run(env.call(token, **read_body("after")))
    assert r.status_code == 401 and r.json()["reason_code"] in ("CREDENTIAL_REVOKED", "AGENT_NOT_BOUND")
    assert not r.json()["executed"]


def test_agent_identity_cannot_be_spoofed_through_body_or_runtime_claims(env):
    research = env.token("research_agent", "runtime_beta")
    # A JSON identity field is rejected, not ignored-and-trusted.
    r = run(env.call(research, **read_body("spoof"), agent_id="operations_agent"))
    assert r.status_code == 422 and r.json()["reason_code"] == "INVALID_ACTION"
    # A credential naming a runtime the agent is not bound to is refused.
    wrong_rt = env.token("operations_agent", "runtime_beta")
    assert run(env.call(wrong_rt, **read_body("rt"))).json()["reason_code"] == "AGENT_NOT_BOUND"
    # Research cannot use operations-only capabilities even with a valid credential.
    r = run(env.call(research, request_id="draft", workflow_id=WORKFLOW, tool="prepare_email", parameters=EMAIL))
    assert r.json()["decision"] == "BLOCK" and r.json()["reason_code"] == "CAPABILITY_NOT_GRANTED"


def test_role_claims_in_tokens_cannot_escalate(env):
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode({"iss": ISSUER, "aud": GATEWAY_AUDIENCE, "sub": "research_agent", "rt": "runtime_beta",
                        "jti": "cred_role", "iat": now, "nbf": now, "exp": now + 300, "typ": "agent-runtime",
                        "role": "operations", "capabilities": ["send_email"]}, env.identity_private, algorithm="EdDSA")
    r = run(env.call(token, request_id="esc", workflow_id=WORKFLOW, tool="prepare_email", parameters=EMAIL))
    assert r.json()["decision"] == "BLOCK" and r.json()["reason_code"] == "CAPABILITY_NOT_GRANTED"


def test_binding_enforces_least_privilege_and_disjoint_approvers(env):
    with pytest.raises(ValueError):
        env.control.bind_agent("research_x", "runtime_beta", "research", [WORKFLOW], ["send_email"], by="admin")
    with pytest.raises(ValueError):
        env.control.bind_agent("operations_agent", "runtime_beta", "operations", [WORKFLOW], None, by="admin")
    with pytest.raises(ValueError):
        env.control.bind_agent(APPROVER, "runtime_beta", "operations", [WORKFLOW], None, by="admin")
    from swarmguard.services.control_plane import ControlPlane
    with pytest.raises(ValueError):   # an agent identity can never be configured as a human approver
        ControlPlane(Database(env.db_url), env.clock, approval_signing_key=env.approval_key,
                     approver_ids=["operations_agent"])


def test_capability_scope_and_workflow_scope_are_enforced(env):
    env.control.bind_agent("ops_limited", "runtime_alpha", "operations", [WORKFLOW], ["read_document"], by="admin")
    limited = env.token("ops_limited")
    assert run(env.call(limited, **read_body("ok"))).json()["decision"] == "BLOCK"   # not a workflow participant
    ops = env.token()
    r = run(env.call(ops, **{**read_body("wf"), "workflow_id": "wf_not_bound"}))
    assert r.json()["reason_code"] == "NOT_WORKFLOW_PARTICIPANT"
    r = run(env.call(limited, request_id="d", workflow_id=WORKFLOW, tool="prepare_email", parameters=EMAIL))
    assert r.json()["reason_code"] == "CAPABILITY_NOT_GRANTED"


# ----------------------------------------------------------------------------------------- authorization

def test_missing_forged_and_mismatched_approvals_are_not_executed(env):
    tok = env.token()
    eid, aid = run(env.draft_and_request(tok))
    missing = run(env.call(tok, **env.send_body("s_missing", eid, None))).json()
    assert missing["decision"] == "ESCALATE" and missing["reason_code"] == "APPROVAL_REQUIRED" and not missing["executed"]
    forged = run(env.call(tok, **env.send_body("s_forged", eid, "approval_999"))).json()
    assert forged["decision"] == "BLOCK" and forged["reason_code"] == "APPROVAL_NOT_FOUND"
    env.approve(aid)
    changed = run(env.call(tok, **env.send_body("s_changed", eid, aid, recipient="other@example.test"))).json()
    assert changed["decision"] == "BLOCK" and changed["reason_code"] in ("APPROVAL_MISMATCH", "REQUEST_CONTENT_MISMATCH")
    stale = run(env.call(tok, **{**env.send_body("s_stale", eid, aid), "resource_version": 2})).json()
    assert stale["decision"] == "BLOCK" and stale["reason_code"] == "RESOURCE_VERSION_MISMATCH"
    assert sent_count(env) == 0


def test_expired_and_revoked_approvals_are_blocked(env):
    tok = env.token()
    eid, aid = run(env.draft_and_request(tok, "e"))
    cred = env.control._approver_credentials[APPROVER]
    packet = env.control.approvals.review_packet(aid)
    env.control.approvals.approve(aid, cred, expected_operation_hash=packet.operation_hash, ttl=timedelta(seconds=-1))
    r = run(env.call(tok, **env.send_body("s_exp", eid, aid))).json()
    assert r["reason_code"] == "APPROVAL_EXPIRED" and not r["executed"]
    eid2, aid2 = run(env.draft_and_request(tok, "v"))
    env.approve(aid2)
    env.control.revoke_approval(aid2, approver_id=APPROVER, reason="operator withdrew consent")
    r = run(env.call(tok, **env.send_body("s_rev", eid2, aid2))).json()
    assert r["decision"] == "BLOCK" and r["reason_code"] == "APPROVAL_REVOKED" and not r["executed"]
    assert sent_count(env) == 0


def test_agent_cannot_tamper_with_approval_parameters_or_expiry(env):
    tok, eid, aid = run(approved_send(env, "t"))
    later = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    env.control.db.execute("UPDATE approvals SET expires_at = ? WHERE approval_id = ?", (later, aid))
    r = run(env.call(tok, **env.send_body("s_t", eid, aid))).json()
    assert r["decision"] == "BLOCK" and r["reason_code"] == "APPROVAL_INTEGRITY_FAILURE" and sent_count(env) == 0


def test_approval_from_another_workflow_cannot_be_reused(env):
    env.control.create_workflow("wf_other", "Other", ["operations_agent"], by="admin")
    tok, eid, aid = run(approved_send(env, "x"))
    env.control.bind_agent("ops_other", "runtime_alpha", "operations", ["wf_other"], None, by="admin")
    other = env.token("ops_other")
    r = run(env.call(other, **{**env.send_body("cross", eid, aid), "workflow_id": "wf_other"})).json()
    assert not r["executed"] and r["decision"] == "BLOCK"


def test_gateway_and_agent_cannot_grant_approvals(env):
    tok = env.token()
    _, aid = run(env.draft_and_request(tok, "g"))
    async def probe():
        async with env.agent(tok) as client:
            return [await client.post(p, json={"decision": "APPROVE"}) for p in
                    (f"/api/approvals/{aid}", f"/api/v1/approvals/{aid}/revoke", "/api/v1/security/emergency-stop")]
    assert all(r.status_code in (404, 405) for r in run(probe()))
    with pytest.raises(PermissionError):
        env.control.grant_approval(aid, approver_id="operations_agent", operation_hash="0" * 64)


def test_ethics_allow_cannot_relax_deterministic_block():
    from swarmguard.models.decisions import PolicyDecision
    policy = PolicyDecision(action_id="a", decision=Decision.BLOCK, reason_code=ReasonCode.APPROVAL_REQUIRED,
                            explanation="x", policy_version="v")
    assert DecisionEngine().combine(policy, None, None).decision is Decision.BLOCK


# ----------------------------------------------------------------------------------------- execution isolation

def test_authorized_operation_executes_once_through_executor(env):
    tok, eid, aid = run(approved_send(env))
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert r["decision"] == "ALLOW" and r["execution_result"]["status"] == "SIMULATED_EMAIL_SENT"
    assert r["execution_result"]["real_delivery"] is False and sent_count(env) == 1
    op = env.control.ops.get(r["operation_id"])
    assert [t["to_state"] for t in env.control.ops.transitions(op["operation_id"])] == \
        ["PENDING", "AUTHORIZED", "EXECUTING", "SUCCEEDED", "SUCCEEDED"]


def test_direct_executor_access_is_denied(env):
    async def probe():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.executor_app), base_url="http://ex") as c:
            out = {"none": await c.post("/internal/v1/execute", json={"tool": "send_email", "authorized": True})}
            agent_token = env.token()
            out["agent_token"] = await c.post("/internal/v1/execute", headers={"Authorization": f"Bearer {agent_token}"})
            forged = issue_grant(other_private_key(), operation_id="operation_001", action_id="action_001", digest="x",
                                 now=datetime.now(UTC))
            out["forged"] = await c.post("/internal/v1/execute", headers={"Authorization": f"Bearer {forged}"})
            unknown = issue_grant(env.grant_private, operation_id="operation_999", action_id="action_999", digest="x",
                                  now=datetime.now(UTC))
            out["valid_key_unknown_op"] = await c.post("/internal/v1/execute", headers={"Authorization": f"Bearer {unknown}"})
            out["docs"] = await c.get("/docs")
            return out
    out = run(probe())
    assert out["none"].status_code == 401 and out["agent_token"].status_code == 401 and out["forged"].status_code == 401
    assert out["valid_key_unknown_op"].status_code == 403 and out["docs"].status_code == 404
    assert sent_count(env) == 0
    alerts = [e for e in env.control.repos.events.query(workflow_id="system_security") if e.event_type.value == "SECURITY_ALERT"]
    assert len(alerts) >= 4


def test_replayed_grant_never_executes_twice(env):
    captured = {}
    tok, eid, aid = run(approved_send(env, "rg"))
    original = env.plane.backend.channel.execute
    async def capture(grant, correlation):
        captured["grant"] = grant
        return await original(grant, correlation)
    env.plane.backend.channel.execute = capture
    assert run(env.call(tok, **env.send_body("send", eid, aid))).json()["executed"]
    async def replay():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.executor_app), base_url="http://ex") as c:
            return await c.post("/internal/v1/execute", headers={"Authorization": f"Bearer {captured['grant']}"})
    r = run(replay())
    assert r.status_code == 200 and r.json()["state"] == "SUCCEEDED" and sent_count(env) == 1


def test_executor_rejects_grant_when_persisted_decision_is_not_allow(env):
    ops = ProtectedOperationRepository(env.control.db)
    tok, eid, aid = run(approved_send(env, "d"))
    original = env.plane.backend.channel.execute
    async def tamper(grant, correlation):
        op = ops.list(state="AUTHORIZED")[0]
        import json as _j
        row = env.control.db.fetchone("SELECT record_json FROM decisions WHERE action_id = ?", (op["action_id"],))
        record = _j.loads(row[0]); record["decision"] = "BLOCK"; record["policy_decision"] = "BLOCK"
        env.control.db.execute("UPDATE decisions SET record_json = ? WHERE action_id = ?", (_j.dumps(record), op["action_id"]))
        return await original(grant, correlation)
    env.plane.backend.channel.execute = tamper
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert not r["executed"] and r["reason_code"] == "EXECUTION_GRANT_INVALID" and sent_count(env) == 0


def test_unknown_tools_and_dynamic_names_are_rejected(env):
    env.control.bind_agent("ops_shell", "runtime_alpha", "operations", [WORKFLOW], None, by="admin")
    for tool in ("run_shell", "__import__", "send_email; drop"):
        r = run(env.call(env.token(), request_id=f"u_{abs(hash(tool))}", workflow_id=WORKFLOW, tool=tool, parameters={}))
        assert r.json()["decision"] == "BLOCK" and not r.json()["executed"]


# ----------------------------------------------------------------------------------------- idempotency & concurrency

def test_duplicate_concurrent_requests_execute_at_most_once(env):
    tok, eid, aid = run(approved_send(env, "dup"))
    async def both():
        return await asyncio.gather(*(env.call(tok, **env.send_body("same_key", eid, aid)) for _ in range(5)))
    responses = run(both())
    executed = [r for r in responses if r.status_code == 200 and r.json().get("executed")]
    assert sent_count(env) == 1 and len(executed) >= 1
    assert all(r.status_code in (200, 409) for r in responses)
    assert all(r.json().get("idempotent_replay") for r in responses if r not in executed[:1])


def test_idempotency_key_reuse_with_different_operation_is_blocked(env):
    tok = env.token()
    assert run(env.call(tok, **read_body("key_1"))).json()["executed"]
    r = run(env.call(tok, **read_body("key_1", resource_id="doc_brief_wf_remote"))).json()
    assert r["decision"] == "BLOCK" and r["reason_code"] == "IDEMPOTENCY_KEY_REUSED"


def test_concurrent_consumption_of_single_use_approval(env):
    tok, eid, aid = run(approved_send(env, "cc"))
    async def race():
        return await asyncio.gather(*(env.call(tok, **env.send_body(f"race_{i}", eid, aid)) for i in range(4)))
    bodies = [r.json() for r in run(race())]
    assert sum(b["executed"] for b in bodies) == 1 and sent_count(env) == 1
    assert {b["reason_code"] for b in bodies if not b["executed"]} <= {"APPROVAL_ALREADY_USED", "EXECUTION_PRECONDITION_FAILED"}


def test_multi_connection_consumption_race(env):
    """Two independent executors (separate DB connections) race on the same approval."""
    from swarmguard.execution.executor import ProtectedExecutor
    tok, eid, aid = run(approved_send(env, "mc"))
    env.build_gateway()
    second = ProtectedExecutor(db=Database(env.db_url, migrate=False), clock=env.clock, grant_public_key=env.grant_public,
                               approval_signing_key=env.approval_key, approver_ids=env.approvers)
    from swarmguard.services.execution_service import create_execution_app
    other_app = create_execution_app(second, heartbeat_seconds=0)
    other = Stage7Env.__new__(Stage7Env)
    other.__dict__.update(env.__dict__)
    other.executor_app = other_app
    other.build_gateway()
    async def race():
        return await asyncio.gather(env.call(tok, **env.send_body("m1", eid, aid)), other.call(tok, **env.send_body("m2", eid, aid)))
    bodies = [r.json() for r in run(race())]
    assert sum(b["executed"] for b in bodies) == 1 and sent_count(env) == 1


def test_resource_change_between_authorization_and_execution_is_rejected(env):
    tok, eid, aid = run(approved_send(env, "rv"))
    original = env.plane.backend.channel.execute
    async def concurrent_change(grant, correlation):
        draft = env.control.workspace.get(eid)
        env.control.workspace.update(eid, {"body": "changed concurrently"}, agent_id="swarmguard.control",
                                     expected_version=draft.version, event_workflow_id=WORKFLOW, enforce_acl=False)
        return await original(grant, correlation)
    env.plane.backend.channel.execute = concurrent_change
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert not r["executed"] and r["reason_code"] == "EXECUTION_PRECONDITION_FAILED" and sent_count(env) == 0
    assert env.control.ops.get(r["operation_id"])["state"] == "FAILED"


def test_workflow_cancelled_while_operation_pending_never_starts(env):
    tok, eid, aid = run(approved_send(env, "wc"))
    original = env.plane.backend.channel.execute
    async def cancel_first(grant, correlation):
        env.control.pause_workflow(WORKFLOW, by="admin", reason="incident")
        return await original(grant, correlation)
    env.plane.backend.channel.execute = cancel_first
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert not r["executed"] and r["reason_code"] == "WORKFLOW_NOT_ACTIVE" and sent_count(env) == 0


# ----------------------------------------------------------------------------------------- failure injection

def test_executor_unavailable_fails_closed_and_is_reported(env):
    def down(request):
        raise httpx.ConnectError("executor down", request=request)
    tok, eid, aid = run(approved_send(env, "ex"))
    env.build_gateway(executor_transport=httpx.MockTransport(down))
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert r["decision"] == "BLOCK" and r["reason_code"] == "EXECUTOR_UNAVAILABLE" and not r["executed"]
    assert env.control.ops.get(r["operation_id"])["state"] == "FAILED" and sent_count(env) == 0
    assert run(env.call(tok, **env.send_body("send", eid, aid))).json()["idempotent_replay"]


def test_lost_reply_after_execution_reports_late_success_not_retry(env):
    tok, eid, aid = run(approved_send(env, "lr"))
    env.plane.backend.fault = "drop_response"
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert r["executed"] and r["execution_state"] == "SUCCEEDED" and sent_count(env) == 1


def test_executor_crash_mid_execution_is_unknown_and_never_replayed(env):
    tok, eid, aid = run(approved_send(env, "cr"))
    def crash(op):
        raise RuntimeError("simulated executor crash after consumption began")
    env.executor.complete = crash
    async def call():
        try:
            return (await env.call(tok, **env.send_body("send", eid, aid))).json()
        except RuntimeError:
            return None
    r = run(call())
    op = env.control.ops.by_request("operations_agent", "send")
    assert op["state"] == "UNKNOWN" and sent_count(env) == 0
    if r is not None:
        assert r["execution_state"] == "UNKNOWN" and r["reason_code"] == "EXECUTION_OUTCOME_UNKNOWN" and not r["executed"]
    again = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert again["idempotent_replay"] and sent_count(env) == 0


def test_gateway_restart_after_authorization_cannot_replay(env):
    captured = {}
    async def crash_after_authorization(grant, correlation):
        captured["grant"] = grant
        raise asyncio.CancelledError
    tok, eid, aid = run(approved_send(env, "gr"))
    env.plane.backend.channel.execute = crash_after_authorization
    with pytest.raises(asyncio.CancelledError):
        run(env.plane.remote.handle(f"Bearer {tok}", env.send_body("send", eid, aid), peer="test"))
    op = env.control.ops.by_request("operations_agent", "send")
    assert op["state"] == "AUTHORIZED"
    env.build_gateway()   # "restart"
    summary = recover_gateway(ProtectedOperationRepository(env.plane.db), env.plane.monitor, env.clock)
    assert summary["authorized_cancelled"] == 1
    async def deliver_old_grant():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.executor_app), base_url="http://ex") as c:
            return await c.post("/internal/v1/execute", headers={"Authorization": f"Bearer {captured['grant']}"})
    late = run(deliver_old_grant())
    assert late.json()["state"] == "FAILED" and sent_count(env) == 0
    replay = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert replay["idempotent_replay"] and not replay["executed"]


def test_executor_restart_recovery_marks_executing_unknown(env):
    tok, eid, aid = run(approved_send(env, "er"))
    env.executor.complete = lambda op: (_ for _ in ()).throw(RuntimeError("crash"))
    # The gateway process also dies before arbitrating (no UNKNOWN written by the gateway).
    env.plane.backend._arbitrate = lambda op_id: (_ for _ in ()).throw(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        run(env.plane.remote.handle(f"Bearer {tok}", env.send_body("send", eid, aid), peer="test"))
    assert env.control.ops.by_request("operations_agent", "send")["state"] == "EXECUTING"
    assert env.executor.recover() == {"executing_marked_unknown": 1}
    assert env.control.ops.by_request("operations_agent", "send")["state"] == "UNKNOWN" and sent_count(env) == 0


def test_policy_engine_failure_fails_closed(env):
    env.plane.gateway._policy.evaluate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("policy down"))
    r = run(env.call(env.token(), **read_body("p"))).json()
    assert r["decision"] == "BLOCK" and r["reason_code"] == "INTERNAL_ERROR" and not r["executed"]


def test_database_unavailable_fails_closed(env):
    env.plane.db.close()
    r = run(env.call(env.token(), **read_body("db")))
    assert r.status_code == 503 and r.json()["reason_code"] == "AUTHORIZATION_SERVICE_UNAVAILABLE"
    assert not r.json()["executed"] and sent_count(env) == 0


# ----------------------------------------------------------------------------------------- operator controls

def test_emergency_stop_blocks_new_operations_and_requires_explicit_recovery(env):
    tok, eid, aid = run(approved_send(env, "es"))
    env.control.activate_emergency_stop(by="admin_test", reason="drill")
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert r["reason_code"] == "EMERGENCY_STOP_ACTIVE" and not r["executed"]
    with pytest.raises(ValueError):
        env.control.recover_emergency_stop(by="admin_test", reason="done", confirm="yes")
    env.control.recover_emergency_stop(by="admin_test", reason="drill over", confirm="RESUME PROTECTED OPERATIONS")
    assert run(env.call(tok, **env.send_body("send2", eid, aid))).json()["executed"]
    kinds = [e.event_type.value for e in env.control.repos.events.query(workflow_id="system_security")]
    assert "EMERGENCY_STOP_ACTIVATED" in kinds and "EMERGENCY_STOP_RECOVERED" in kinds


def test_emergency_stop_also_enforced_at_executor(env):
    tok, eid, aid = run(approved_send(env, "ee"))
    original = env.plane.backend.channel.execute
    async def stop_then_deliver(grant, correlation):
        env.control.activate_emergency_stop(by="admin_test", reason="race")
        return await original(grant, correlation)
    env.plane.backend.channel.execute = stop_then_deliver
    r = run(env.call(tok, **env.send_body("send", eid, aid))).json()
    assert r["reason_code"] == "EMERGENCY_STOP_ACTIVE" and not r["executed"] and sent_count(env) == 0


def test_disabled_capability_is_refused(env):
    env.control.set_capability("read_document", enabled=False, by="admin", reason="maintenance")
    assert run(env.call(env.token(), **read_body("dis"))).json()["reason_code"] == "CAPABILITY_DISABLED"
    env.control.set_capability("read_document", enabled=True, by="admin", reason="restored")
    assert run(env.call(env.token(), **read_body("en"))).json()["executed"]


def test_rate_limits_and_request_size(env):
    env.build_gateway(agent_requests_per_minute=2)
    tok = env.token()
    codes = [run(env.call(tok, **read_body(f"rl_{i}"))).status_code for i in range(3)]
    assert codes[-1] == 429
    async def big():
        async with env.agent(tok) as c:
            return await c.post("/api/v1/capabilities/execute", content=b"{" + b" " * 70_000 + b"}",
                                headers={"content-type": "application/json"})
    assert run(big()).status_code == 413


def test_health_and_readiness(env):
    async def probe(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc") as c:
            return (await c.get("/health/live")).status_code, (await c.get("/health/ready"))
    live, ready = run(probe(env.gateway_app))
    assert live == 200 and ready.status_code == 200 and ready.json()["checks"] == {"database": "ok", "executor": "ok"}
    assert "database_url" not in ready.text and env.db_url not in ready.text
    env.build_gateway(executor_transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r))))
    live, ready = run(probe(env.gateway_app))
    assert live == 200 and ready.status_code == 503 and ready.json()["checks"]["executor"] == "failed"
    live, ready = run(probe(env.executor_app))
    assert live == 200 and ready.status_code == 200


def test_gateway_exposes_no_administrative_or_audit_mutation_routes(env):
    routes = {getattr(r, "path", "") for r in env.gateway_app.routes}
    assert routes == {"/health/live", "/health/ready", "/api/v1/capabilities/execute"}
    assert {getattr(r, "path", "") for r in env.executor_app.routes} == {"/health/live", "/health/ready",
                                                                        "/internal/v1/execute"}


# ----------------------------------------------------------------------------------------- audit integrity

def chain_db(tmp_path):
    from swarmguard.agents.simulator import SwarmSimulator
    from tests.conftest import approve, make_email, send
    sim = SwarmSimulator(str(tmp_path / "audit.db"))
    sim.start_workflow("wf_a", "audit", purpose="service_updates")
    eid = make_email(sim, "wf_a")
    send(sim, "wf_a", eid, 1, approve(sim, "wf_a", eid))
    return sim


def test_chain_has_stable_contiguous_sequence_and_validates(tmp_path):
    sim = chain_db(tmp_path)
    report = verify_chain(sim.db)
    seqs = [int(r[0]) for r in sim.db.fetchall("SELECT seq FROM audit_chain ORDER BY seq")]
    assert report.status == "PASS" and seqs == list(range(1, len(seqs) + 1)) and report.chained_events == len(seqs)


@pytest.mark.parametrize("attack,kind", [
    ("UPDATE events SET summary = 'nothing happened' WHERE seq = 5", "ALTERED_EVENT"),
    ("DELETE FROM events WHERE seq = 4", "MISSING_EVENT"),
    ("DELETE FROM audit_chain WHERE seq = 4", "SEQUENCE_GAP"),
    ("UPDATE audit_chain SET previous_hash = '00' WHERE seq = 6", "BROKEN_LINK"),
])
def test_modified_missing_and_broken_links_are_detected(tmp_path, attack, kind):
    sim = chain_db(tmp_path)
    sim.db.execute(attack)
    report = verify_chain(sim.db)
    assert report.status == "FAIL" and kind in {f["kind"] for f in report.failures}


def test_reordered_events_are_detected(tmp_path):
    sim = chain_db(tmp_path)
    with sim.db.transaction():
        sim.db.execute("UPDATE events SET seq = -1 WHERE seq = 3")
        sim.db.execute("UPDATE events SET seq = 3 WHERE seq = 4")
        sim.db.execute("UPDATE events SET seq = 4 WHERE seq = -1")
    kinds = {f["kind"] for f in verify_chain(sim.db).failures}
    assert "REORDERED_EVENT" in kinds


def test_unchained_insertion_and_truncated_start_are_detected(tmp_path):
    sim = chain_db(tmp_path)
    row = dict(zip(sim.db.fetchone("SELECT * FROM events WHERE seq = 2").keys(), sim.db.fetchone("SELECT * FROM events WHERE seq = 2")))
    sim.db.execute("INSERT INTO events(event_id, seq, workflow_id, event_type, agent_id, actor_type, summary, created_at) "
                   "VALUES ('event_forged', 999, 'wf_a', 'ACTION_EXECUTED', 'x', 'SYSTEM', 'forged', ?)", (row["created_at"],))
    assert "UNCHAINED_EVENT" in {f["kind"] for f in verify_chain(sim.db).failures}
    sim.db.execute("DELETE FROM events WHERE event_id = 'event_forged'")
    sim.db.execute("DELETE FROM audit_chain WHERE seq <= 2")
    sim.db.execute("DELETE FROM events WHERE seq <= 2")
    assert "BROKEN_LINK" in {f["kind"] for f in verify_chain(sim.db).failures}


def test_full_chain_rewrite_is_only_detected_with_external_checkpoint(tmp_path):
    sim = chain_db(tmp_path)
    write_checkpoint(sim.db, tmp_path / "checkpoints")
    sim.db.execute("UPDATE events SET summary = 'rewritten history' WHERE seq = 5")
    # Attacker with write access recomputes every hash after the change.
    links = sim.db.fetchall("SELECT seq, event_id FROM audit_chain ORDER BY seq")
    previous = "0" * 64
    from swarmguard.storage.repositories import AuditChainRepository
    events = {r["event_id"]: r for r in AuditChainRepository(sim.db).event_rows()}
    for seq, event_id in links:
        payload = payload_hash(events[event_id])
        digest = link_hash(seq, event_id, payload, previous)
        sim.db.execute("UPDATE audit_chain SET payload_hash = ?, previous_hash = ?, event_hash = ? WHERE seq = ?",
                       (payload, previous, digest, seq))
        previous = digest
    assert verify_chain(sim.db).status == "PASS"   # documented limitation: undetectable without a checkpoint
    report = verify_chain(sim.db, load_checkpoints(tmp_path / "checkpoints"))
    assert report.status == "FAIL" and {f["kind"] for f in report.failures} == {"CHECKPOINT_MISMATCH"}


def test_audit_chain_repository_offers_no_mutation_interface():
    from swarmguard.storage.repositories import AuditChainRepository
    public = {n for n in dir(AuditChainRepository) if not n.startswith("_")}
    assert public == {"append", "head", "links", "event_rows", "link_at"}


# ----------------------------------------------------------------------------------------- package boundaries

def _imports(path):
    import ast
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_untrusted_agent_package_has_no_trusted_code_or_keys():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "swarmguard_agent"
    for path in root.rglob("*.py"):
        imports = _imports(path)
        assert not imports & {"swarmguard", "swarmguard_services", "swarmguard_providers", "jwt", "cryptography",
                              "psycopg", "sqlite3"}, path


def test_network_packages_never_import_email_or_smtp():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    banned = {"smtplib", "imaplib", "poplib", "email", "ftplib", "subprocess"}
    for package in ("swarmguard_services", "swarmguard_agent"):
        for path in (root / package).rglob("*.py"):
            assert not _imports(path) & banned, path
