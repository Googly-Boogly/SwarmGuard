"""Canonical event serialization and SHA-256 chain links.

``payload_hash = SHA256(canonical(event row))`` and
``event_hash = SHA256(canonical({seq, event_id, payload_hash, previous_hash}))``.
The first chained event links to ``GENESIS_HASH``. Only hashing lives here; SQL lives in
``storage/repositories.py``. A chain stored in the same database an attacker controls is
tamper-*evident* only relative to an external checkpoint (see ``verification.py``).
"""
from __future__ import annotations
import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64
CHAIN_ALGORITHM = "sha256-canonical-json-v1"
# Exact ``events`` columns covered by the payload hash (stored representation).
EVENT_COLUMNS = ("event_id", "seq", "workflow_id", "event_type", "agent_id", "actor_type", "action_id", "tool",
                 "resource_id", "resource_version_before", "resource_version_after", "approval_id", "decision",
                 "reason_code", "parent_event_ids_json", "related_event_ids_json", "summary", "details_json",
                 "created_at")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def payload_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(canonical({c: row[c] for c in EVENT_COLUMNS})).hexdigest()


def link_hash(seq: int, event_id: str, payload: str, previous: str) -> str:
    return hashlib.sha256(canonical({"seq": int(seq), "event_id": event_id, "payload_hash": payload,
                                     "previous_hash": previous})).hexdigest()
