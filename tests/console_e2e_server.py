"""Explicit, isolated Playwright backend. Never provisions the normal operator or database.

Stage 7: the same temporary database is pre-populated by real remote operations through the gateway and
executor (ASGI transports), and the console is served with the control plane and role accounts. The legacy
password-only login (human_operator: RESEARCHER+OPERATOR) still works for the earlier specs.
"""
from __future__ import annotations
import asyncio
import os
import tempfile
from pathlib import Path
import uvicorn
from swarmguard.console.api import create_console_app
from swarmguard.console.auth import OPERATOR, LEGACY_ROLES, OperatorAccount, password_hash

PASSWORD = 'browser-test-operator-password'


async def seed_stage7(env) -> None:
    tok = env.token()
    await env.call(tok, request_id='e2e_read', workflow_id='wf_remote', tool='read_document', resource_id='doc_service_faq')
    eid, aid = await env.draft_and_request(tok, 'e2e')
    await env.call(tok, **env.send_body('e2e_send_unapproved', eid, None))
    env.approve(aid)
    await env.call(tok, **env.send_body('e2e_send', eid, aid))
    await env.call(None, request_id='e2e_noauth', workflow_id='wf_remote', tool='read_document')


if __name__ == '__main__':
    if os.environ.get('SWARMGUARD_E2E') != '1':
        raise SystemExit('This isolated test server requires SWARMGUARD_E2E=1')
    with tempfile.TemporaryDirectory(prefix='swarmguard-browser-') as root:
        from tests.stage7_support import Stage7Env
        db = Path(root) / 'browser.db'
        env = Stage7Env(Path(root), db_url=str(db))
        asyncio.run(seed_stage7(env))
        digest = password_hash(PASSWORD)
        accounts = {OPERATOR: OperatorAccount(OPERATOR, digest, LEGACY_ROLES),
                    'admin_ada': OperatorAccount('admin_ada', digest, frozenset({'ADMINISTRATOR'})),
                    'auditor_aki': OperatorAccount('auditor_aki', digest, frozenset({'AUDITOR'})),
                    'operator_alice': OperatorAccount('operator_alice', digest, frozenset({'OPERATOR'}))}
        local_access = os.environ.get('SWARMGUARD_E2E_LOCAL_ACCESS') == '1'
        app = create_console_app(db, accounts=accounts, control_plane=None if local_access else env.control,
                                 local_access=local_access, checkpoint_dir=Path(root) / 'checkpoints',
                                 secure_cookie=True, step_delay=.06, code_revision='unavailable (isolated browser test)')
        uvicorn.run(app, host='127.0.0.1', port=8000, ws_max_size=16384, limit_concurrency=32)
