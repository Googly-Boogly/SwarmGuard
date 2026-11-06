"""Explicit, isolated Playwright backend. Never provisions the normal operator or database."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
import uvicorn
from swarmguard.console.api import create_console_app
from swarmguard.console.auth import password_hash

if __name__ == '__main__':
    if os.environ.get('SWARMGUARD_E2E') != '1':
        raise SystemExit('This isolated test server requires SWARMGUARD_E2E=1')
    with tempfile.TemporaryDirectory(prefix='swarmguard-browser-') as root:
        app=create_console_app(Path(root)/'browser.db',encoded_password=password_hash('browser-test-operator-password'),
                               secure_cookie=True,step_delay=.06,code_revision='unavailable (isolated browser test)')
        uvicorn.run(app,host='127.0.0.1',port=8000,ws_max_size=16384,limit_concurrency=32)
