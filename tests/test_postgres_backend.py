"""PostgreSQL backend (opt-in): set SWARMGUARD_TEST_POSTGRES_URL to a *disposable loopback* server, e.g.
postgresql://sgtest@127.0.0.1:55432/sgtest with SWARMGUARD_DB_PASSWORD_FILE. Each test uses a fresh database."""
from __future__ import annotations
import asyncio
import os
import secrets
import threading
from urllib.parse import urlsplit, urlunsplit
import pytest

URL = os.environ.get("SWARMGUARD_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SWARMGUARD_TEST_POSTGRES_URL not set (opt-in PostgreSQL tests)")


@pytest.fixture
def pg_url():
    import psycopg
    from swarmguard.storage.database import _pg_password
    parts = urlsplit(URL)
    assert parts.hostname in ("127.0.0.1", "localhost", "::1"), "PostgreSQL tests only run against loopback servers"
    name = "sg_test_" + secrets.token_hex(6)
    with psycopg.connect(URL, autocommit=True, password=_pg_password()) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    yield urlunsplit(parts._replace(path=f"/{name}"))
    with psycopg.connect(URL, autocommit=True, password=_pg_password()) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_forward_migrations_are_idempotent_and_tracked(pg_url):
    from swarmguard.storage.database import LATEST_MIGRATION, Database
    db = Database(pg_url)
    assert db.applied_migrations() == list(range(1, LATEST_MIGRATION + 1)) and db.pending_migrations() == []
    db.next_id("x")
    again = Database(pg_url)
    assert again.apply_migrations() == [] and again.next_id("x") == "x_002"
    unmigrated = Database(pg_url, migrate=False)
    assert unmigrated.pending_migrations() == []


def test_stage1_authorization_flow_and_audit_chain_on_postgres(pg_url):
    from swarmguard.agents.simulator import SwarmSimulator
    from swarmguard.audit.verification import verify_chain
    from tests.conftest import approve, make_email, send
    sim = SwarmSimulator(pg_url)
    sim.start_workflow("wf_pg", "pg", purpose="service_updates")
    eid = make_email(sim, "wf_pg")
    aid = approve(sim, "wf_pg", eid)
    assert send(sim, "wf_pg", eid, 1, aid).executed
    assert send(sim, "wf_pg", eid, 1, aid).reason_code.value == "APPROVAL_ALREADY_USED"
    assert send(sim, "wf_pg", eid, 1, None, recipient="other@example.test").executed is False
    assert len(sim.sent_emails("wf_pg")) == 1 and verify_chain(sim.db).status == "PASS"


def test_remote_gateway_and_executor_on_postgres(pg_url, tmp_path):
    from tests.stage7_support import Stage7Env
    env = Stage7Env(tmp_path, db_url=pg_url)
    async def flow():
        tok = env.token()
        eid, aid = await env.draft_and_request(tok)
        env.approve(aid)
        first = (await env.call(tok, **env.send_body("s1", eid, aid))).json()
        race = await asyncio.gather(*(env.call(tok, **env.send_body(f"r{i}", eid, aid)) for i in range(3)))
        return first, [r.json() for r in race]
    first, race = asyncio.run(flow())
    assert first["executed"] and not any(r["executed"] for r in race)
    assert len(env.control.db.fetchall("SELECT * FROM sent_emails")) == 1


def test_cross_connection_single_use_consumption_is_serialized(pg_url):
    """Advisory-lock transactions serialize writers across independent connections (BEGIN IMMEDIATE analogue)."""
    from swarmguard.storage.database import Database
    Database(pg_url).execute("INSERT INTO counters(name, value) VALUES ('race', 0)")
    winners = []

    def worker() -> None:
        db = Database(pg_url, migrate=False)
        with db.transaction():
            (value,) = db.fetchone("SELECT value FROM counters WHERE name = 'race'")
            if value == 0:
                db.execute("UPDATE counters SET value = 1 WHERE name = 'race'")
                winners.append(threading.get_ident())
        db.close()
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1


def test_least_privilege_roles(pg_url):
    import psycopg
    from swarmguard.storage.database import Database
    from swarmguard.storage.postgres_roles import ROLE_WRITES, apply_role_grants
    db = Database(pg_url)
    for role in ROLE_WRITES:
        db.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                   f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")
    apply_role_grants(db)
    def attempt(role: str, sql: str) -> bool:
        try:
            with db.transaction():
                db.execute(f"SET LOCAL ROLE {role}")
                db.execute(sql)
                raise _Rollback
        except _Rollback:
            return True
        except psycopg.errors.InsufficientPrivilege:
            return False
    insert_email = ("INSERT INTO sent_emails(email_id, action_id, workflow_id, approval_id, resource_id, resource_version, "
                    "recipient, subject, body, status, sent_at) VALUES ('e','a','w','ap','r',1,'x','s','b','S','t')")
    assert attempt("sg_executor", insert_email) is True
    assert attempt("sg_gateway", insert_email) is False
    assert attempt("sg_control", insert_email) is False
    for role in ROLE_WRITES:
        assert attempt(role, "UPDATE events SET summary = 'x'") is False
        assert attempt(role, "DELETE FROM audit_chain") is False
        assert attempt(role, "DELETE FROM approvals") is False
        assert attempt(role, "CREATE TABLE evil(x int)") is False
    assert attempt("sg_gateway", "UPDATE service_runtimes SET status = 'ACTIVE'") is False   # identities: control only
    assert attempt("sg_control", "INSERT INTO protected_operations(operation_id) VALUES ('x')") is False


class _Rollback(Exception):
    pass
