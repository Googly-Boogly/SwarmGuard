"""Local console entry point; run one worker only (job ownership is process-local)."""
from __future__ import annotations
import os
import subprocess
from swarmguard.console.api import create_console_app


def code_revision() -> str:
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True, timeout=3)
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


from pathlib import Path

# Provider injection happens here, at the entry point: swarmguard/ never imports network code.
from swarmguard_providers.rq1 import make_rq1_llm

rq1_llm_factory, rq1_llm_status = make_rq1_llm()

app = create_console_app(os.environ.get("SWARMGUARD_CONSOLE_DB", "swarmguard_console.db"),
                         operator_file=Path(os.environ.get("SWARMGUARD_OPERATOR_FILE", ".console/operator.json")),
                         secure_cookie=os.environ.get("SWARMGUARD_COOKIE_SECURE", "true").lower() != "false",
                         local_access=os.environ.get("SWARMGUARD_LOCAL_ACCESS", "true").lower() == "true",
                         code_revision=code_revision(), rq1_llm_factory=rq1_llm_factory,
                         rq1_llm_status=rq1_llm_status)
