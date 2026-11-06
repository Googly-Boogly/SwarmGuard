"""SwarmGuard read-only observability API.

    python demo.py              # populate swarmguard_demo.db
    uvicorn main:app --reload   # binds 127.0.0.1:8000 by default
    python main.py              # same, honoring SWARMGUARD_API_HOST / SWARMGUARD_API_PORT

Environment: SWARMGUARD_DB_PATH (default swarmguard_demo.db), SWARMGUARD_LOG_LEVEL.
"""

from __future__ import annotations

from swarmguard.api import create_app
from swarmguard.config import Settings, configure_logging

settings = Settings.from_env()
configure_logging(settings.log_level)
app = create_app(settings.db_path)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port)
