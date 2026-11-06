"""Trusted agent identities and opaque credentials.

The simulator registers each agent once and hands the agent a gateway client
bound to an opaque credential. The gateway resolves identity *only* from that
credential; any identity an agent writes into a request is untrusted data.

Limitation: inside one Python process this is an architectural boundary, not a
cryptographic one. Scripted Stage 1 agents do not attempt introspection; real
isolation (separate processes / signed tokens) is future work.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from enum import StrEnum

from swarmguard.models.actions import ID_PATTERN


class AgentRole(StrEnum):
    COORDINATOR = "coordinator"
    RESEARCH = "research"
    OPERATIONS = "operations"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: str
    role: AgentRole
    display_name: str


@dataclass(frozen=True, slots=True)
class TrustedAgentContext:
    """Per-request runtime context built by the gateway, never by agents.

    ``permissions`` come from the policy engine (the authoritative source) and
    ``authorized_workflows`` from trusted workflow membership. Nothing in this
    object is derived from agent-supplied request fields.
    """

    identity: AgentIdentity
    permissions: frozenset[str]
    authorized_workflows: frozenset[str]

    @property
    def agent_id(self) -> str:
        return self.identity.agent_id

    @property
    def role(self) -> AgentRole:
        return self.identity.role


class AgentCredential:
    """Opaque bearer credential. The token is never logged or serialized."""

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "AgentCredential(<redacted>)"

    def __reduce__(self):  # pragma: no cover - defensive
        raise TypeError("credentials are not serializable")


class RegistrySealedError(RuntimeError):
    pass


class IdentityRegistry:
    """Maps credentials to identities. Sealed after simulator setup."""

    def __init__(self) -> None:
        self._by_token: dict[str, AgentIdentity] = {}
        self._ids: set[str] = set()
        self._sealed = False

    def register(self, identity: AgentIdentity) -> AgentCredential:
        if self._sealed:
            raise RegistrySealedError("identity registry is sealed; agents cannot be added at runtime")
        if not re.fullmatch(ID_PATTERN, identity.agent_id):
            raise ValueError("invalid agent id")
        if identity.agent_id in self._ids:
            raise ValueError(f"agent id already registered: {identity.agent_id}")
        token = secrets.token_urlsafe(32)
        self._by_token[token] = identity
        self._ids.add(identity.agent_id)
        return AgentCredential(token)

    def seal(self) -> None:
        self._sealed = True

    @property
    def sealed(self) -> bool:
        return self._sealed

    def resolve(self, credential: object) -> AgentIdentity | None:
        if not isinstance(credential, AgentCredential):
            return None
        for token, identity in self._by_token.items():
            if secrets.compare_digest(token, credential.token):
                return identity
        return None

    def get(self, agent_id: str) -> AgentIdentity | None:
        return next((i for i in self._by_token.values() if i.agent_id == agent_id), None)

    def is_agent(self, agent_id: str) -> bool:
        return agent_id in self._ids

    def agent_ids(self) -> list[str]:
        return sorted(self._ids)
