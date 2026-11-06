"""Gateway -> executor execution grants: EdDSA JWTs bound to one durable operation.

A grant alone authorizes nothing: the executor additionally requires the matching AUTHORIZED
``protected_operations`` row, the persisted ALLOW decision, and an authorization digest it recomputes
from the stored action. Possessing the signing key without database write access, or database access
without the key, is insufficient to execute.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from swarmguard.models.actions import ActionRequest

GRANT_ISSUER = "swarmguard-gateway"
GRANT_AUDIENCE = "swarmguard-executor"
GRANT_TTL = timedelta(seconds=30)
MAX_GRANT_LIFETIME = timedelta(seconds=120)


class GrantError(PermissionError):
    pass


@dataclass(frozen=True)
class GrantClaims:
    operation_id: str
    action_id: str
    digest: str
    expires: datetime


def authorization_digest(request: ActionRequest, expected: dict[str, Any]) -> str:
    """Digest of exactly what was authorized: the trusted action plus the gateway's expectations."""
    body = {"action_id": request.action_id, "workflow_id": request.workflow_id, "agent_id": request.agent_id,
            "tool": request.tool, "resource_id": request.resource_id, "resource_version": request.resource_version,
            "parameters": request.parameters, "approval_id": request.approval_id, "expected": expected}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def issue_grant(key: Ed25519PrivateKey, *, operation_id: str, action_id: str, digest: str, now: datetime,
                ttl: timedelta = GRANT_TTL) -> str:
    if ttl > MAX_GRANT_LIFETIME:
        raise ValueError("grant lifetime exceeds policy")
    claims = {"iss": GRANT_ISSUER, "aud": GRANT_AUDIENCE, "op": operation_id, "act": action_id, "dig": digest,
              "jti": operation_id, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return jwt.encode(claims, key, algorithm="EdDSA")


def verify_grant(key: Ed25519PublicKey, token: str | None, *, now: datetime) -> GrantClaims:
    if not token or len(token) > 2048:
        raise GrantError("missing or malformed execution grant")
    try:
        claims = jwt.decode(token, key, algorithms=["EdDSA"], audience=GRANT_AUDIENCE, issuer=GRANT_ISSUER, leeway=2,
                            options={"require": ["iss", "aud", "op", "act", "dig", "iat", "exp", "jti"],
                                     "verify_iat": False}, )
    except jwt.InvalidTokenError as exc:
        raise GrantError(f"invalid execution grant ({type(exc).__name__})") from None
    if claims["exp"] - claims["iat"] > MAX_GRANT_LIFETIME.total_seconds():
        raise GrantError("execution grant lifetime exceeds policy")
    if claims["exp"] + 2 < now.timestamp():
        raise GrantError("execution grant expired")
    return GrantClaims(str(claims["op"]), str(claims["act"]), str(claims["dig"]),
                       datetime.fromtimestamp(claims["exp"], now.tzinfo))
