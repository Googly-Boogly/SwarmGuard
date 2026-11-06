"""uvicorn swarmguard_services.control_main:app  (control service = research console + Stage 7 control plane).

Hardened mode: no in-process synthetic execution, no migrations (the database role has no DDL rights),
operator accounts from a provisioned file of scrypt hashes, external audit checkpoint directory.
"""
from __future__ import annotations
import os
from pathlib import Path
from swarmguard.clock import SystemClock
from swarmguard.console.api import create_console_app
from swarmguard.console.auth import load_accounts
from swarmguard.services.control_plane import ControlPlane
from swarmguard.services.settings import CommonSettings
from swarmguard.storage.database import Database

common = CommonSettings.from_env()
clock = SystemClock()
accounts = load_accounts(Path(os.environ["SWARMGUARD_OPERATORS_FILE"]))
approvers = [a for a in common.approvers if a in accounts and "OPERATOR" in accounts[a].roles]
control = ControlPlane(Database(common.database_url, migrate=False), clock, approval_signing_key=common.approval_key,
                       approver_ids=approvers)
checkpoints = os.environ.get("SWARMGUARD_CHECKPOINT_DIR")
app = create_console_app(common.database_url, accounts=accounts, control_plane=control, clock=clock,
                         checkpoint_dir=Path(checkpoints) if checkpoints else None, inprocess_execution=False,
                         secure_cookie=os.environ.get("SWARMGUARD_COOKIE_SECURE", "true").lower() != "false",
                         code_revision=os.environ.get("SWARMGUARD_CODE_REVISION", "unavailable"))
