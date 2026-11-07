"""Local access removes password prompts while retaining browser request controls."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from swarmguard.clock import ManualClock
from swarmguard.console.api import create_console_app
from swarmguard.console.auth import COOKIE

ORIGIN = {'Origin': 'http://localhost:5173'}


def test_local_session_opens_without_credentials_and_keeps_csrf_and_roles(tmp_path):
    clock = ManualClock()
    app = create_console_app(tmp_path / 'local.db', local_access=True, clock=clock)
    with TestClient(app, base_url='https://localhost') as client:
        assert app.state.auth.accounts == {}
        initial = client.get('/api/session').json()
        assert not initial['authenticated']
        assert not initial['login_required']
        assert not initial['login_available']
        assert client.get('/api/research/lab/catalog').status_code == 401
        assert client.post('/api/session/local', json={}).status_code == 403
        assert client.post('/api/session/local', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/session/local', content='{}', headers=ORIGIN).status_code in (415, 422)
        for forged in ({'username': 'research_agent'}, {'roles': ['ADMINISTRATOR']}, {'approver_id': 'fake'}):
            assert client.post('/api/session/local', json=forged, headers=ORIGIN).status_code == 422

        response = client.post('/api/session/local', json={}, headers=ORIGIN)
        assert response.status_code == 200
        session = response.json()
        assert session['authenticated']
        assert session['operator_id'] == 'human_operator'
        assert set(session['roles']) == {'RESEARCHER', 'OPERATOR'}
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'Secure' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']
        assert COOKIE not in session
        assert client.get('/api/research/lab/catalog').status_code == 200
        # Reopening reuses the session rather than consuming another slot.
        assert client.post('/api/session/local', json={}, headers=ORIGIN).json() == session
        assert len(app.state.auth.sessions) == 1
        assert client.post('/api/session', json={'password': 'anything'}, headers=ORIGIN).status_code == 404
        headers = {**ORIGIN, 'X-CSRF-Token': session['csrf_token']}
        for bad in (ORIGIN, {**headers, 'X-CSRF-Token': 'bad'}, {**headers, 'Origin': 'https://evil.example'}):
            assert client.post('/api/workflows', json={'scenario_ids': ['legitimate_collaboration']}, headers=bad).status_code == 403
        # Administrator authority is never implied by password-free access.
        assert client.post('/api/v1/security/emergency-stop', json={'reason': 'test', 'confirm': True}, headers=headers).status_code == 403
        # A real researcher command uses the existing bounded job service.
        result = client.post('/api/research/lab/experiments', json={
            'dataset': 'swarmguard_autonomous', 'max_total_trials': 2,
        }, headers=headers)
        assert result.status_code == 202
        assert result.json()['created_by'] == 'human_operator'

        clock.advance(timedelta(hours=2))
        assert not client.get('/api/session').json()['authenticated']
        assert client.post('/api/research/lab/experiments', json={}, headers=headers).status_code == 401
        renewed = client.post('/api/session/local', json={}, headers=ORIGIN).json()
        assert renewed['authenticated']
        assert renewed['csrf_token'] != session['csrf_token']


def test_local_session_is_disabled_in_password_mode(tmp_path):
    with TestClient(create_console_app(tmp_path / 'password.db', accounts={})) as client:
        assert client.get('/api/session').json()['login_required']
        assert client.post('/api/session/local', json={}, headers=ORIGIN).status_code == 404


@pytest.mark.parametrize('options', [{'inprocess_execution': False}, {'control_plane': object()}])
def test_local_access_cannot_enable_hardened_control_authority(tmp_path, options):
    with pytest.raises(ValueError, match='in-process synthetic console'):
        create_console_app(tmp_path / 'invalid.db', local_access=True, **options)
