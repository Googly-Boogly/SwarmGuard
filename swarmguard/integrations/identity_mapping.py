"""Host-registered object identity, never display-name or model-supplied metadata."""
from __future__ import annotations


class IdentityMapping:
    def __init__(self, authorized_ids: set[str]) -> None:
        self._authorized = frozenset(authorized_ids)
        self._objects: list[tuple[object, str]] = []

    def register(self, agent: object, trusted_id: str) -> str:
        if isinstance(agent, (str, int, float, bool, dict, list, type(None))):
            raise ValueError('register a host-owned framework object, not an untrusted agent name or argument')
        if trusted_id not in self._authorized:
            raise ValueError('identity was not registered by the sealed host runtime')
        if any(obj is agent or aid == trusted_id for obj, aid in self._objects):
            raise ValueError('duplicate or ambiguous identity mapping')
        self._objects.append((agent, trusted_id))
        return trusted_id

    def resolve(self, agent: object) -> str:
        for obj, aid in self._objects:
            if obj is agent:
                return aid
        raise PermissionError('unregistered framework object; names and metadata cannot authenticate it')

    def registered(self) -> list[str]:
        return [aid for _, aid in self._objects]
