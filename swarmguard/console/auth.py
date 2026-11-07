"""Operator accounts, roles and short-lived server-side sessions (loopback console / control service).

Stage 7 adds role-based accounts (RESEARCHER, OPERATOR, ADMINISTRATOR, AUDITOR) with separation of
duties: no role implies another, and no account receives ADMINISTRATOR unless explicitly provisioned.
The legacy single-password file (Stage 4.5) maps to one ``human_operator`` account with RESEARCHER and
OPERATOR only. Passwords are stored only as salted scrypt hashes. MFA is not implemented.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException, Request
from swarmguard.clock import Clock
from swarmguard.infrastructure.rate_limits import RateLimiter
from swarmguard.models.actions import ID_PATTERN

COOKIE = "swarmguard_operator"
OPERATOR = "human_operator"  # legacy fixed trusted registry identity; never supplied by browser
ALLOWED_ORIGINS = frozenset({f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 8000)} |
                            {o for o in os.environ.get("SWARMGUARD_ALLOWED_ORIGINS", "").split(",") if o})
ROLES = ("RESEARCHER", "OPERATOR", "ADMINISTRATOR", "AUDITOR")
LEGACY_ROLES = frozenset({"RESEARCHER", "OPERATOR"})
SESSION_LIFETIME = timedelta(hours=1)


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, _ = encoded.split("$")
        return algorithm == "scrypt" and hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def load_password(path: Path) -> str | None:
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    encoded = record.get("password_hash")
    if not isinstance(encoded, str) or not encoded.startswith("scrypt$"):
        raise ValueError("invalid operator password hash; rerun setup_console_operator.py")
    return encoded


@dataclass(frozen=True)
class OperatorAccount:
    username: str
    password_hash: str = field(repr=False)
    roles: frozenset[str]

    def __post_init__(self) -> None:
        if not re.fullmatch(ID_PATTERN, self.username):
            raise ValueError("invalid operator username")
        if not self.roles or not self.roles <= set(ROLES):
            raise ValueError(f"roles must be a non-empty subset of {ROLES}")
        if not self.password_hash.startswith("scrypt$"):
            raise ValueError("password must be stored as an scrypt hash")


def load_accounts(path: Path) -> dict[str, OperatorAccount]:
    """``{"accounts": [{"username", "roles": [...], "password_hash"}]}`` written by setup_console_operator.py."""
    if not path.exists():
        return {}
    record = json.loads(path.read_text())
    accounts = {}
    for entry in record.get("accounts", []):
        account = OperatorAccount(entry["username"], entry["password_hash"], frozenset(entry["roles"]))
        if account.username in accounts:
            raise ValueError(f"duplicate operator account {account.username}")
        accounts[account.username] = account
    return accounts


@dataclass
class OperatorSession:
    csrf: str
    expires: float
    username: str = OPERATOR
    roles: frozenset[str] = LEGACY_ROLES


class OperatorAuth:
    def __init__(self, credentials: str | dict[str, OperatorAccount] | None, clock: Clock,
                 *, local_access: bool = False) -> None:
        if isinstance(credentials, str):
            self.accounts = {OPERATOR: OperatorAccount(OPERATOR, credentials, LEGACY_ROLES)}
        else:
            self.accounts = dict(credentials or {})
        self.clock = clock
        self.local_access = local_access
        self.sessions: dict[str, OperatorSession] = {}
        self.failures: list[float] = []
        self._user_failures: dict[str, list[float]] = {}
        self._mutations = RateLimiter(capacity=60, refill_per_second=1.0)
        self.audit_hook = None   # optional callable(kind, username, detail) set by the control plane

    @property
    def password(self) -> str | None:   # backwards-compatible "login configured" flag
        return "configured" if self.accounts else None

    def _audit(self, kind: str, username: str, detail: str) -> None:
        if self.audit_hook is not None:
            try:
                self.audit_hook(kind, username, detail)
            except Exception:  # noqa: BLE001 - auditing failures never grant or block access; but are logged
                logging.getLogger("swarmguard.console.auth").exception("operator audit hook failed for %s", kind)

    def origin(self, request: Request) -> None:
        if request.headers.get("origin") not in ALLOWED_ORIGINS:
            raise HTTPException(403, "local same-origin operator request required")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON content type required")

    def login(self, request: Request, password: str, username: str | None = None) -> str:
        self.origin(request)
        if self.local_access:
            raise HTTPException(404, "password login is disabled for the local console")
        now = self.clock.now().timestamp()
        name = username or OPERATOR
        self.failures = [t for t in self.failures if t > now - 60]
        user_failures = [t for t in self._user_failures.get(name, []) if t > now - 300]
        self._user_failures[name] = user_failures
        if len(self.failures) >= 5 or len(user_failures) >= 5:
            raise HTTPException(429, "too many login attempts; retry later")
        if not self.accounts:
            raise HTTPException(503, "operator login not configured; run setup_console_operator.py locally")
        account = self.accounts.get(name)
        # Always run one scrypt verification so unknown usernames cost the same as wrong passwords.
        valid = verify_password(password, account.password_hash if account else password_hash("x" * 16))
        if account is None or not valid:
            self.failures.append(now)
            user_failures.append(now)
            self._audit("LOGIN_FAILED", name if account else "unknown_account", "invalid operator credentials")
            raise HTTPException(401, "invalid operator credentials")
        return self._new_session(account.username, account.roles, "LOGIN")

    def open_local_session(self, request: Request) -> str | None:
        if not self.local_access:
            raise HTTPException(404, "local console access is disabled")
        self.origin(request)
        if self.session(request):
            return None
        return self._new_session(OPERATOR, LEGACY_ROLES, "LOCAL_SESSION_CREATED")

    def _new_session(self, username: str, roles: frozenset[str], event: str) -> str:
        now = self.clock.now().timestamp()
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires > now}
        if len(self.sessions) >= 32:
            raise HTTPException(429, "operator session limit reached")
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = OperatorSession(
            csrf=secrets.token_urlsafe(32), expires=now + SESSION_LIFETIME.total_seconds(),
            username=username, roles=roles)
        self._audit(event, username, f"roles {sorted(roles)}")
        return token

    def session(self, request: Request) -> OperatorSession | None:
        token = request.cookies.get(COOKIE, "")
        key = hashlib.sha256(token.encode()).hexdigest()
        session = self.sessions.get(key)
        if session and session.expires <= self.clock.now().timestamp():
            self.sessions.pop(key, None)
            return None
        return session

    def require(self, request: Request, role: str | tuple[str, ...] | None = None) -> OperatorSession:
        session = self.session(request)
        if not session:
            raise HTTPException(401, "operator login required")
        self.origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "CSRF token missing or invalid")
        self.check_role(session, role)
        if not self._mutations.allow(session.username):
            raise HTTPException(429, "operator request rate exceeded")
        return session

    def require_read(self, request: Request, role: str | tuple[str, ...] | None = None) -> OperatorSession:
        session = self.session(request)
        if not session:
            raise HTTPException(401, "operator login required")
        self.check_role(session, role)
        return session

    def check_role(self, session: OperatorSession, role: str | tuple[str, ...] | None) -> None:
        if role is None:
            return
        wanted = (role,) if isinstance(role, str) else role
        if not session.roles & set(wanted):
            self._audit("ROLE_DENIED", session.username, f"required one of {list(wanted)}")
            raise HTTPException(403, f"role {' or '.join(wanted)} required")

    def revoke_user_sessions(self, username: str) -> int:
        doomed = [k for k, v in self.sessions.items() if v.username == username]
        for key in doomed:
            self.sessions.pop(key, None)
        return len(doomed)

    def logout(self, request: Request) -> None:
        self.require(request)
        token = request.cookies.get(COOKIE, "")
        self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
