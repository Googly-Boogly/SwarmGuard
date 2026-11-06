"""Stage 6 console boundary and persisted API flows (offline, isolated databases)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from swarmguard.console.api import create_console_app
from swarmguard.console.auth import password_hash
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository

PASSWORD = "isolated-stage6-operator-password"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture(autouse=True)
def framework_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('CREWAI_STORAGE_DIR', str(tmp_path / 'framework-data'))
    monkeypatch.setenv('OTEL_SDK_DISABLED', 'true')
    monkeypatch.setenv('CREWAI_TELEMETRY_ENABLED', 'false')


@pytest.fixture
def client(tmp_path):
    app = create_console_app(tmp_path / 'console.db', encoded_password=password_hash(PASSWORD), step_delay=.001)
    with TestClient(app, base_url='https://localhost') as c:
        c.db_path = tmp_path / 'console.db'
        yield c


def login(c):
    response = c.post('/api/session', json={"password": PASSWORD}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return {**ORIGIN, "X-CSRF-Token": response.json()['csrf_token']}


def wait(c, jid, statuses=('COMPLETED', 'FAILED', 'TIMED_OUT', 'CANCELLED')):
    record = None
    for _ in range(1500):
        record = c.get(f'/api/platform/jobs/{jid}').json()
        if record['status'] in statuses:
            return record
        time.sleep(.01)
    pytest.fail(f'job did not reach {statuses}: {record}')


MUTATIONS = [
    ('/api/integrations/runs', {"case": "service"}),
    ('/api/evaluation/frameworks/runs', {}),
    ('/api/regression/runs', {"baseline_id": "baseline_001"}),
    ('/api/regression/baselines', {"experiment_id": "framework_eval_001"}),
    ('/api/platform/jobs/platform_job_001/cancel', {}),
]


@pytest.mark.parametrize('endpoint,body', MUTATIONS)
def test_stage6_mutations_require_researcher_session(client, endpoint, body):
    assert client.post(endpoint, json=body, headers=ORIGIN).status_code == 401


@pytest.mark.parametrize('endpoint,body', MUTATIONS)
def test_stage6_mutations_require_csrf_and_local_origin(client, endpoint, body):
    h = login(client)
    for headers in (ORIGIN, {**h, 'X-CSRF-Token': 'bad'}, {**h, 'Origin': 'https://evil.example'}):
        assert client.post(endpoint, json=body, headers=headers).status_code == 403


@pytest.mark.parametrize('path', ['/api/integrations', '/api/runtime/events', '/api/platform/jobs',
                                  '/api/regression/baselines', '/api/regression/runs', '/api/evaluation/frameworks',
                                  '/api/evaluation/runtime-overhead'])
def test_stage6_reads_require_session(client, path):
    assert client.get(path).status_code == 401
    login(client)
    assert client.get(path).status_code == 200


@pytest.mark.parametrize('body', [
    {"case": "service", "agent_id": "operations_agent"},
    {"case": "service", "trusted_agent_id": "human_operator"},
    {"case": "service", "config": {"framework": "custom", "agent_id": "coordinator"}},
    {"case": "service", "config": {"framework": "custom", "tools": ["send_email"]}},
    {"case": "service", "config": {"framework": "custom", "policy": {}}},
    {"case": "service", "approver": "human_operator"},
    {"case": "run_shell"},
    {"case": "service", "config": {"framework": "custom", "model_identifier": "gpt-live"}},
])
def test_browser_cannot_supply_identities_tools_policies_or_live_models(client, body):
    h = login(client)
    assert client.post('/api/integrations/runs', json=body, headers=h).status_code == 422
    assert PlatformRepository(Database(client.db_path)).list('stage6_job') == []


def test_browser_cannot_weaken_mandatory_regression_invariant(client):
    h = login(client)
    for policy in ({"maximum_prohibited_outcomes": 1}, {"version": "lenient"}):
        r = client.post('/api/regression/runs', headers=h, json={"baseline_id": "baseline_001", "policy": policy})
        assert r.status_code == 422


def test_interactive_integration_uses_existing_operator_approval(client):
    h = login(client)
    r = client.post('/api/integrations/runs', headers=h, json={"case": "service", "human_approval": "INTERACTIVE"})
    assert r.status_code == 202, r.text
    jid = r.json()['job_id']
    wait(client, jid, ('WAITING_APPROVAL',))
    approval = next(a for a in client.get('/api/approvals').json() if a['actionable'])
    # The browser approves through the existing Stage 4.5 exact-operation interface only.
    granted = client.post('/api/approvals/' + approval['approval_id'], headers=h,
                          json={"decision": "APPROVE", "operation_hash": approval['operation_hash'], "confirmed": True})
    assert granted.status_code == 200, granted.text
    job = wait(client, jid)
    assert job['status'] == 'COMPLETED', job
    row = job['result']
    assert row['legitimate_completed'] is True and row['prohibited_outcome'] is False
    events = client.get(f"/api/integrations/{row['integration_id']}/events").json()
    assert any(e['source'] == 'GATEWAY_VERIFIED' and e['metadata'].get('executed') for e in events)
    assert {e['seq'] for e in client.get('/api/runtime/events').json()} >= {e['seq'] for e in events}
    detail = client.get(f"/api/integrations/{row['integration_id']}").json()
    assert detail['status'] == 'DISCONNECTED' and detail['registered_agents']


def test_forged_approval_integration_is_blocked_and_visible(client):
    h = login(client)
    jid = client.post('/api/integrations/runs', headers=h, json={"case": "approval_attack", "human_approval": "SIMULATED"}).json()['job_id']
    row = wait(client, jid)['result']
    assert row['prohibited_outcome'] is False
    last = row['decisions'][-1]
    assert last['tool'] == 'send_email' and last['decision'] == 'BLOCK' and not last['executed']


def test_evaluation_baseline_and_regression_flow_matches_persisted_records(client):
    h = login(client)
    jid = client.post('/api/evaluation/frameworks/runs', headers=h, json={"framework": "custom"}).json()['job_id']
    evaluation = wait(client, jid)['result']
    assert evaluation['status'] == 'COMPLETED'
    baseline = client.post('/api/regression/baselines', headers=h, json={"experiment_id": evaluation['experiment_id']})
    assert baseline.status_code == 201, baseline.text
    bid = baseline.json()['baseline_id']
    assert baseline.json()['approved_by'] == 'human_operator'
    jid = client.post('/api/regression/runs', headers=h, json={"baseline_id": bid, "config": {
        "framework": "custom", "profile": "redirected", "application_version": "fixture-redirected-v2"}}).json()['job_id']
    report = wait(client, jid)['result']
    assert report['classification'] == 'CONFIRMED_THRESHOLD_VIOLATION' and report['exit_code'] == 1
    assert report['mandatory_violation'] and not report['ci_passed']
    api_report = client.get(f"/api/regression/runs/{report['run_id']}/report").json()
    stored = PlatformRepository(Database(client.db_path)).get('regression', report['run_id'])[0]
    assert api_report == stored == report
    # A run with a prohibited outcome can never become an official baseline.
    refused = client.post('/api/regression/baselines', headers=h, json={"experiment_id": report['candidate_id']})
    assert refused.status_code == 422
