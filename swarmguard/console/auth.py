"""Password hashes and short-lived, server-side operator sessions for loopback use."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException, Request
from swarmguard.clock import Clock

COOKIE = "swarmguard_operator"
OPERATOR = "human_operator"  # fixed trusted registry identity; never supplied by browser
ALLOWED_ORIGINS = frozenset(f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 8000))


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


@dataclass
class OperatorSession:
    csrf: str
    expires: float


class OperatorAuth:
    def __init__(self, encoded_password: str | None, clock: Clock) -> None:
        self.password = encoded_password
        self.clock = clock
        self.sessions: dict[str, OperatorSession] = {}
        self.failures: list[float] = []

    def origin(self, request: Request) -> None:
        if request.headers.get("origin") not in ALLOWED_ORIGINS:
            raise HTTPException(403, "local same-origin operator request required")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON content type required")

    def login(self, request: Request, password: str) -> str:
        self.origin(request)
        now = self.clock.now().timestamp()
        self.failures = [t for t in self.failures if t > now - 60]
        if len(self.failures) >= 5:
            raise HTTPException(429, "too many login attempts; retry in one minute")
        if self.password is None:
            raise HTTPException(503, "operator login not configured; run setup_console_operator.py locally")
        if not verify_password(password, self.password):
            self.failures.append(now)
            raise HTTPException(401, "invalid operator password")
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires > now}
        if len(self.sessions) >= 32:
            raise HTTPException(429, "operator session limit reached")
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = OperatorSession(
            csrf=secrets.token_urlsafe(32), expires=now + timedelta(hours=1).total_seconds())
        return token

    def session(self, request: Request) -> OperatorSession | None:
        token = request.cookies.get(COOKIE, "")
        key = hashlib.sha256(token.encode()).hexdigest()
        session = self.sessions.get(key)
        if session and session.expires <= self.clock.now().timestamp():
            self.sessions.pop(key, None)
            return None
        return session

    def require(self, request: Request) -> OperatorSession:
        session = self.session(request)
        if not session:
            raise HTTPException(401, "operator login required")
        self.origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "CSRF token missing or invalid")
        return session

    def logout(self, request: Request) -> None:
        self.require(request)
        token = request.cookies.get(COOKIE, "")
        self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
