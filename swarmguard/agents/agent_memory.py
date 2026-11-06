"""Bounded private working memory for one autonomous agent.

* Private: only the owning agent's observations include it. It is persisted in ``agent_memory``,
  keyed by (workflow, agent).
* Bounded: the most recent ``max_entries`` entries, truncated to ``max_chars`` in total. Older entries
  leave the context, but identifiers they mention (actions, events, approvals, messages, tasks,
  resource versions) stay in ``pinned_references``.
* Memory is the agent's own working state. It is NOT an audit record. The audit trail is the
  events, actions and decisions written by trusted code.
"""

from __future__ import annotations

import json
import re
from typing import Any

from swarmguard.clock import Clock
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import Repositories

_REF = re.compile(r"\b(?:action|event|approval|message|task|email|sent)_\d{3,}\b|\b[a-z]+_[a-z0-9_]+@v\d+\b")


class AgentMemory:
    def __init__(self, repos: Repositories, db: Database, clock: Clock, workflow_id: str, agent_id: str, *,
                 max_entries: int = 24, max_chars: int = 12_000) -> None:
        self._repos, self._db, self._clock = repos, db, clock
        self.workflow_id, self.agent_id = workflow_id, agent_id
        self.max_entries, self.max_chars = max_entries, max_chars
        self._entries: list[dict[str, Any]] = []
        self._pinned: list[str] = []

    def add(self, kind: str, content: dict[str, Any]) -> None:
        entry = {"kind": kind, **content}
        self._entries.append(entry)
        self._repos.agent_memory.insert(self._db.next_id("memory"), self.workflow_id, self.agent_id, kind,
                                        json.dumps(entry, sort_keys=True, default=str), self._clock.now().isoformat())
        if len(self._entries) > self.max_entries:
            for old in self._entries[:-self.max_entries]:
                self._pin(old)
            self._entries = self._entries[-self.max_entries:]

    def _pin(self, entry: dict[str, Any]) -> None:
        for ref in _REF.findall(json.dumps(entry, default=str)):
            if ref not in self._pinned:
                self._pinned.append(ref)

    def entries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        used = 0
        for e in reversed(self._entries):
            size = len(json.dumps(e, default=str))
            if used + size > self.max_chars and out:
                self._pin(e)
                continue
            out.append(e)
            used += size
        return list(reversed(out))

    @property
    def pinned_references(self) -> list[str]:
        return list(self._pinned[-200:])

    def all_persisted(self) -> list[dict[str, Any]]:
        return self._repos.agent_memory.memory_for(self.workflow_id, self.agent_id)
