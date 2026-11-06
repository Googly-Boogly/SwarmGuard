"""uvicorn swarmguard_services.executor_main:app  (trusted protected executor; one worker)."""
from __future__ import annotations
from swarmguard.services.execution_service import app_from_env

app = app_from_env()
