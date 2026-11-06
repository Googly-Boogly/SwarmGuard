"""Authenticated agent-runtime identities (Stage 7).

Credentials are short-lived EdDSA (Ed25519) JWTs issued by a trusted offline issuer (the operator's
host CLI) and verified by the gateway with the *public* key only. One credential names exactly one
trusted agent (``sub``) and its parent runtime (``rt``). Authorization facts (role, workflows,
capabilities) are never read from the token; they come from the server-side ``agent_bindings`` row.

Boundary assumption: a runtime that holds the credentials of several agents can act as any of them.
Agents are cryptographically distinguishable only when each agent process holds only its own token.
Authentication identifies the caller; it does not make the requested operation safe.
"""
from __future__ import annotations
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from swarmguard.models.actions import ID_PATTERN
from swarmguard.models.decisions import ReasonCode
from swarmguard.security.identity import AgentRole
from swarmguard.security.policy_engine import DEFAULT_POLICY, SecurityPolicy
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import ServiceIdentityRepository

ISSUER = "swarmguard-identity"
GATEWAY_AUDIENCE = "swarmguard-gateway"
ALGORITHM = "EdDSA"
MAX_CREDENTIAL_LIFETIME = timedelta(hours=24)
DEFAULT_CREDENTIAL_LIFETIME = timedelta(hours=1)
CLOCK_LEEWAY_SECONDS = 5
MAX_TOKEN_LENGTH = 2048
_REQUIRED = ["exp", "iat", "nbf", "iss", "aud", "sub", "jti", "rt"]


class AuthenticationError(PermissionError):
    def __init__(self, reason: ReasonCode, detail: str, *, agent_hint: str | None = None) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.agent_hint = agent_hint  # unverified; for audit text only, never for authorization


@dataclass(frozen=True, slots=True)
class AuthenticatedAgent:
    """Server-side resolved identity. Built only by ``ServiceIdentityVerifier``."""
    agent_id: str
    runtime_id: str
    role: AgentRole
    workflows: frozenset[str]
    capabilities: frozenset[str]
    credential_id: str
    expires_at: datetime


class CredentialIssuer:
    """Trusted issuer. Holds the private key; must never run inside an agent runtime."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._key = private_key

    def issue(self, *, agent_id: str, runtime_id: str, lifetime: timedelta = DEFAULT_CREDENTIAL_LIFETIME,
              now: datetime | None = None) -> tuple[str, str]:
        if not (timedelta(seconds=1) <= lifetime <= MAX_CREDENTIAL_LIFETIME):
            raise ValueError("credential lifetime must be between 1 second and 24 hours")
        for value in (agent_id, runtime_id):
            if not re.fullmatch(ID_PATTERN, value):
                raise ValueError("invalid identifier")
        now = now or datetime.now(UTC)
        jti = "cred_" + secrets.token_hex(16)
        claims = {"iss": ISSUER, "aud": GATEWAY_AUDIENCE, "sub": agent_id, "rt": runtime_id, "jti": jti,
                  "iat": int(now.timestamp()), "nbf": int(now.timestamp()), "exp": int((now + lifetime).timestamp()),
                  "typ": "agent-runtime"}
        return jwt.encode(claims, self._key, algorithm=ALGORITHM), jti


class ServiceIdentityVerifier:
    """Gateway-side verification: signature, audience, issuer, expiry, lifetime, revocation, binding."""

    def __init__(self, public_key: Ed25519PublicKey, db: Database) -> None:
        self._key = public_key
        self._repo = ServiceIdentityRepository(db)

    def verify(self, authorization_header: str | None, *, now: datetime | None = None) -> AuthenticatedAgent:
        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "missing bearer credential")
        token = authorization_header[len("Bearer "):].strip()
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "malformed credential")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != ALGORITHM:
                raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "unsupported credential algorithm")
            claims = jwt.decode(token, self._key, algorithms=[ALGORITHM], audience=GATEWAY_AUDIENCE, issuer=ISSUER,
                                leeway=CLOCK_LEEWAY_SECONDS, options={"require": _REQUIRED})
        except jwt.ExpiredSignatureError:
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "credential expired") from None
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED,
                                      f"invalid credential ({type(exc).__name__})") from None
        if claims.get("typ") != "agent-runtime":
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "wrong credential type")
        if claims["exp"] - claims["iat"] > MAX_CREDENTIAL_LIFETIME.total_seconds():
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "credential lifetime exceeds policy")
        agent_id, runtime_id, jti = claims["sub"], claims["rt"], claims["jti"]
        if not all(isinstance(v, str) and re.fullmatch(ID_PATTERN, v) for v in (agent_id, runtime_id)) or \
                not isinstance(jti, str):
            raise AuthenticationError(ReasonCode.AUTHENTICATION_FAILED, "malformed credential subject")
        if self._repo.credential_revoked(jti):
            raise AuthenticationError(ReasonCode.CREDENTIAL_REVOKED, "credential revoked", agent_hint=agent_id)
        runtime = self._repo.runtime(runtime_id)
        if runtime is None or runtime["status"] != "ACTIVE":
            raise AuthenticationError(ReasonCode.CREDENTIAL_REVOKED, "runtime identity unknown or revoked",
                                      agent_hint=agent_id)
        binding = self._repo.binding(agent_id)
        if binding is None or binding["status"] != "ACTIVE":
            raise AuthenticationError(ReasonCode.AGENT_NOT_BOUND, "agent identity unknown or revoked", agent_hint=agent_id)
        if binding["runtime_id"] != runtime_id:
            raise AuthenticationError(ReasonCode.AGENT_NOT_BOUND, "agent is bound to a different runtime",
                                      agent_hint=agent_id)
        return AuthenticatedAgent(agent_id=agent_id, runtime_id=runtime_id, role=AgentRole(binding["role"]),
                                  workflows=frozenset(binding["workflows"]),
                                  capabilities=frozenset(binding["capabilities"]), credential_id=jti,
                                  expires_at=datetime.fromtimestamp(claims["exp"], UTC))


def assert_disjoint_approvers(db: Database, approver_ids: list[str] | tuple[str, ...]) -> None:
    """Human approver IDs must never coincide with any agent identity (bound or simulator-registered)."""
    agents = {b["agent_id"] for b in ServiceIdentityRepository(db).bindings()}
    agents |= {str(r[0]) for r in db.fetchall("SELECT agent_id FROM agent_registry")}
    clash = sorted(set(approver_ids) & agents)
    if clash:
        raise ValueError(f"approver IDs collide with agent identities: {clash}")


class IdentityAdministration:
    """Trusted administrative registration. Called only by the control plane / host CLI."""

    def __init__(self, db: Database, policy: SecurityPolicy = DEFAULT_POLICY) -> None:
        self._repo = ServiceIdentityRepository(db)
        self._policy = policy

    def register_runtime(self, runtime_id: str, description: str, *, by: str) -> dict[str, Any]:
        if not re.fullmatch(ID_PATTERN, runtime_id):
            raise ValueError("invalid runtime id")
        if self._repo.runtime(runtime_id) is not None:
            raise ValueError("runtime already registered")
        self._repo.create_runtime(runtime_id, description[:200], created_by=by, at=datetime.now(UTC).isoformat())
        return self._repo.runtime(runtime_id)  # type: ignore[return-value]

    def bind_agent(self, agent_id: str, runtime_id: str, role: str, workflows: list[str],
                   capabilities: list[str] | None, *, by: str) -> dict[str, Any]:
        """Least privilege: capabilities must be a subset of the role's policy permissions."""
        if not re.fullmatch(ID_PATTERN, agent_id) or not all(re.fullmatch(ID_PATTERN, w) for w in workflows):
            raise ValueError("invalid identifier")
        runtime = self._repo.runtime(runtime_id)
        if runtime is None or runtime["status"] != "ACTIVE":
            raise ValueError("runtime is not registered and active")
        if self._repo.binding(agent_id) is not None:
            raise ValueError("agent already bound (bindings are never reassigned; revoke and use a new agent id)")
        agent_role = AgentRole(role)
        allowed = self._policy.permissions_for(agent_role)
        granted = set(capabilities) if capabilities is not None else set(allowed)
        if not granted <= allowed:
            raise ValueError(f"capabilities exceed role permissions: {sorted(granted - allowed)}")
        self._repo.bind_agent(agent_id, runtime_id, agent_role.value, workflows, sorted(granted), created_by=by,
                              at=datetime.now(UTC).isoformat())
        return self._repo.binding(agent_id)  # type: ignore[return-value]
