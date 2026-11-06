"""Audit-chain verification and external checkpoints.

What verification detects (within the chained range): altered event content, altered or reordered
links, missing chain rows (sequence gaps), deleted events, events inserted without a link, and a
truncated chain start. What it cannot detect without an external checkpoint: an attacker with write
access to the database who recomputes the *entire* chain. A checkpoint file stored outside the audited
database (e.g. offline media or a separate trust domain) pins ``(seq, event_hash)`` so that such a
rewrite of any prefix up to the checkpoint is detected.
"""
from __future__ import annotations
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from swarmguard.audit.hash_chain import CHAIN_ALGORITHM, GENESIS_HASH, link_hash, payload_hash
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import AuditChainRepository

MAX_REPORTED_FAILURES = 50


@dataclass
class VerificationReport:
    status: str                      # PASS | FAIL
    algorithm: str
    chained_events: int
    latest_verified_seq: int | None
    head_hash: str | None
    legacy_unchained_events: int     # events written before the chain existed (pre-Stage-7 databases)
    checkpoints_checked: int
    failures: list[dict[str, Any]] = field(default_factory=list)
    verified_at: str = ""
    limitations: str = ("Tamper-evident relative to external checkpoints only; a full-chain rewrite by a "
                        "database writer is undetectable without one.")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_chain(db: Database, checkpoints: list[dict[str, Any]] | None = None) -> VerificationReport:
    repo = AuditChainRepository(db)
    links = repo.links()
    events = {row["event_id"]: row for row in repo.event_rows()}
    failures: list[dict[str, Any]] = []

    def fail(kind: str, seq: int | None, detail: str) -> None:
        if len(failures) < MAX_REPORTED_FAILURES:
            failures.append({"kind": kind, "seq": seq, "detail": detail})

    first_seq = links[0]["seq"] if links else None
    previous = GENESIS_HASH
    expected_seq = first_seq
    latest_ok: int | None = None
    for link in links:
        seq = int(link["seq"])
        if seq != expected_seq:
            fail("SEQUENCE_GAP", seq, f"expected chain seq {expected_seq}, found {seq}")
        if link["previous_hash"] != previous:
            fail("BROKEN_LINK", seq, "previous_hash does not match the preceding link")
        if link_hash(seq, link["event_id"], link["payload_hash"], link["previous_hash"]) != link["event_hash"]:
            fail("LINK_HASH_MISMATCH", seq, "link record altered")
        event = events.get(link["event_id"])
        if event is None:
            fail("MISSING_EVENT", seq, f"event {link['event_id']} referenced by the chain is absent")
        else:
            if int(event["seq"]) != seq:
                fail("REORDERED_EVENT", seq, f"event {link['event_id']} has seq {event['seq']}")
            if payload_hash(event) != link["payload_hash"]:
                fail("ALTERED_EVENT", seq, f"content of event {link['event_id']} differs from its hash")
        if not failures:
            latest_ok = seq
        previous = link["event_hash"]
        expected_seq = seq + 1
    chained_ids = {link["event_id"] for link in links}
    legacy = 0
    for event_id, event in events.items():
        if event_id in chained_ids:
            continue
        if first_seq is not None and int(event["seq"]) < first_seq:
            legacy += 1
        else:
            fail("UNCHAINED_EVENT", int(event["seq"]), f"event {event_id} has no chain link")
    by_seq = {int(link["seq"]): link for link in links}
    for cp in checkpoints or []:
        link = by_seq.get(int(cp["seq"]))
        if link is None:
            fail("CHECKPOINT_MISSING", int(cp["seq"]), "checkpointed sequence is no longer in the chain")
        elif link["event_hash"] != cp["event_hash"]:
            fail("CHECKPOINT_MISMATCH", int(cp["seq"]), "chain differs from the external checkpoint (rewrite)")
    return VerificationReport(
        status="FAIL" if failures else "PASS", algorithm=CHAIN_ALGORITHM, chained_events=len(links),
        latest_verified_seq=latest_ok, head_hash=links[-1]["event_hash"] if links else None,
        legacy_unchained_events=legacy, checkpoints_checked=len(checkpoints or []), failures=failures,
        verified_at=datetime.now(UTC).isoformat())


def write_checkpoint(db: Database, directory: Path) -> Path:
    """Write the current head as a checkpoint file outside the database (exclusive create)."""
    head = AuditChainRepository(db).head()
    if head is None:
        raise ValueError("audit chain is empty; nothing to checkpoint")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"checkpoint_{head[0]:08d}.json"
    record = {"seq": head[0], "event_hash": head[1], "algorithm": CHAIN_ALGORITHM,
              "created_at": datetime.now(UTC).isoformat(), "database": db.display_path}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(record, out, indent=2, sort_keys=True)
    return path


def load_checkpoints(directory: Path | None) -> list[dict[str, Any]]:
    if directory is None or not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("checkpoint_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record.get("seq"), int) or not isinstance(record.get("event_hash"), str):
            raise ValueError(f"malformed checkpoint {path.name}")
        out.append({**record, "file": path.name})
    return out
