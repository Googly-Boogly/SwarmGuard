"""uvicorn swarmguard_services.gateway_main:app  (trusted gateway; one worker)."""
from __future__ import annotations
import os
from swarmguard.services.gateway_service import config_from_env, create_gateway_app
from swarmguard_services.http_channel import HttpExecutorChannel

app = create_gateway_app(config_from_env(HttpExecutorChannel(os.environ["SWARMGUARD_EXECUTOR_URL"],
                                                             timeout=float(os.environ.get("SWARMGUARD_EXECUTOR_TIMEOUT", "5")))))
