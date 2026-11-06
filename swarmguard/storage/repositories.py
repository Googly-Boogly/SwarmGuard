"""Repositories: the only place (besides ``database.py``) that contains SQL.

Each repository maps one table group to Pydantic models. Repositories hold no
authorization logic. They are used only by trusted components and are never
handed to agents.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from swarmguard.models.actions import ActionRequest

from swarmguard.models.approvals import ApprovalRecord, ApprovalStatus
from swarmguard.models.assessments import CouncilEvaluationRecord, CouncilReport
from swarmguard.models.decisions import DecisionRecord
from swarmguard.models.messages import AgentMessage, MessageType
from swarmguard.models.events import ActorType, EventType, WorkflowEvent
from swarmguard.models.workflows import (
    ResourceACL,
    ResourceType,
    ResourceVersion,
    SharedResource,
    WorkflowRecord,
    WorkflowStatus,
)
from swarmguard.audit.hash_chain import EVENT_COLUMNS, GENESIS_HASH, link_hash, payload_hash
from swarmguard.storage.database import INTEGRITY_ERRORS, OPERATIONAL_ERRORS, Database


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class WorkflowRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, record: WorkflowRecord) -> None:
        self._db.execute(
            "INSERT INTO workflows(workflow_id, title, status, participants_json, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record.workflow_id, record.title, record.status.value, _json(record.participants),
             _json(record.metadata), record.created_at.isoformat()),
        )

    def get(self, workflow_id: str) -> WorkflowRecord | None:
        row = self._db.fetchone("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
        return self._from_row(row) if row else None

    def list(self) -> list[WorkflowRecord]:
        return [self._from_row(r) for r in self._db.fetchall("SELECT * FROM workflows ORDER BY created_at, workflow_id")]

    def set_status(self, workflow_id: str, status: WorkflowStatus) -> None:
        self._db.execute("UPDATE workflows SET status = ? WHERE workflow_id = ?", (status.value, workflow_id))

    @staticmethod
    def _from_row(row: Any) -> WorkflowRecord:
        return WorkflowRecord(
            workflow_id=row["workflow_id"], title=row["title"], status=WorkflowStatus(row["status"]),
            participants=json.loads(row["participants_json"]), metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class ResourceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, resource_id: str) -> SharedResource | None:
        row = self._db.fetchone("SELECT * FROM resources WHERE resource_id = ?", (resource_id,))
        return self._from_row(row) if row else None

    def list(self, resource_type: ResourceType | None = None, workflow_id: str | None = None) -> list[SharedResource]:
        sql, params = "SELECT * FROM resources WHERE 1=1", []
        if resource_type is not None:
            sql += " AND resource_type = ?"
            params.append(resource_type.value)
        if workflow_id is not None:
            sql += " AND workflow_id = ?"
            params.append(workflow_id)
        return [self._from_row(r) for r in self._db.fetchall(sql + " ORDER BY resource_id", params)]

    def insert(self, resource: SharedResource) -> None:
        self._db.execute(
            "INSERT INTO resources(resource_id, resource_type, workflow_id, version, content_json, acl_json, "
            "created_by, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (resource.resource_id, resource.resource_type.value, resource.workflow_id, resource.version,
             _json(resource.content), resource.acl.model_dump_json(), resource.created_by, resource.updated_by,
             resource.created_at.isoformat(), resource.updated_at.isoformat()),
        )

    def compare_and_set(
        self, resource_id: str, *, expected_version: int, content: dict[str, Any], agent_id: str, at: datetime
    ) -> bool:
        """Optimistic concurrency: update only if the stored version is still ``expected_version``."""
        cursor = self._db.execute(
            "UPDATE resources SET content_json = ?, version = version + 1, updated_by = ?, updated_at = ? "
            "WHERE resource_id = ? AND version = ?",
            (_json(content), agent_id, at.isoformat(), resource_id, expected_version),
        )
        return cursor.rowcount == 1

    def insert_version(self, v: ResourceVersion) -> None:
        self._db.execute(
            "INSERT INTO resource_versions(resource_id, version, content_json, changes_json, agent_id, action_id, "
            "event_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (v.resource_id, v.version, _json(v.content), _json(v.changes), v.agent_id, v.action_id, v.event_id,
             v.created_at.isoformat()),
        )

    def versions(self, resource_id: str) -> list[ResourceVersion]:
        rows = self._db.fetchall(
            "SELECT * FROM resource_versions WHERE resource_id = ? ORDER BY version", (resource_id,)
        )
        return [
            ResourceVersion(
                resource_id=r["resource_id"], version=r["version"], content=json.loads(r["content_json"]),
                changes=json.loads(r["changes_json"]), agent_id=r["agent_id"], action_id=r["action_id"],
                event_id=r["event_id"], created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    @staticmethod
    def _from_row(row: Any) -> SharedResource:
        return SharedResource(
            resource_id=row["resource_id"], resource_type=ResourceType(row["resource_type"]),
            workflow_id=row["workflow_id"], version=row["version"], content=json.loads(row["content_json"]),
            acl=ResourceACL.model_validate_json(row["acl_json"]), created_by=row["created_by"],
            updated_by=row["updated_by"], created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class ActionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, request: ActionRequest) -> None:
        self._db.execute(
            "INSERT INTO actions(action_id, workflow_id, agent_id, tool, resource_id, resource_version, "
            "parent_action_ids_json, approval_id, request_id, request_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request.action_id, request.workflow_id, request.agent_id, request.tool, request.resource_id,
             request.resource_version, _json(request.parent_action_ids), request.approval_id, request.request_id,
             request.model_dump_json(), request.created_at.isoformat()),
        )

    def get(self, action_id: str) -> ActionRequest | None:
        row = self._db.fetchone("SELECT request_json FROM actions WHERE action_id = ?", (action_id,))
        return ActionRequest.model_validate_json(row["request_json"]) if row else None

    def for_workflow(self, workflow_id: str) -> list[ActionRequest]:
        rows = self._db.fetchall(
            "SELECT request_json FROM actions WHERE workflow_id = ? ORDER BY created_at, action_id", (workflow_id,)
        )
        return [ActionRequest.model_validate_json(r["request_json"]) for r in rows]

    def request_id_used(self, agent_id: str, request_id: str, *, excluding_action_id: str) -> bool:
        row = self._db.fetchone(
            "SELECT 1 FROM actions WHERE agent_id = ? AND request_id = ? AND action_id != ? LIMIT 1",
            (agent_id, request_id, excluding_action_id),
        )
        return row is not None


class DecisionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, record: DecisionRecord) -> None:
        self._db.execute(
            "INSERT INTO decisions(decision_id, action_id, workflow_id, agent_id, tool, decision, reason_code, "
            "record_json, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(decision_id) DO UPDATE SET record_json = excluded.record_json",
            (record.decision_id, record.action_id, record.workflow_id, record.agent_id, record.tool,
             record.decision.value, record.reason_code.value, record.model_dump_json(), record.decided_at.isoformat()),
        )

    def get(self, decision_id: str) -> DecisionRecord | None:
        row = self._db.fetchone("SELECT record_json FROM decisions WHERE decision_id = ?", (decision_id,))
        return DecisionRecord.model_validate_json(row["record_json"]) if row else None

    def for_action(self, action_id: str) -> DecisionRecord | None:
        row = self._db.fetchone("SELECT record_json FROM decisions WHERE action_id = ?", (action_id,))
        return DecisionRecord.model_validate_json(row["record_json"]) if row else None

    def for_workflow(self, workflow_id: str) -> list[DecisionRecord]:
        rows = self._db.fetchall(
            "SELECT record_json FROM decisions WHERE workflow_id = ? ORDER BY decided_at, decision_id", (workflow_id,)
        )
        return [DecisionRecord.model_validate_json(r["record_json"]) for r in rows]


class AuditChainRepository:
    """Append-only chain links for ``events``. No update or delete methods exist by design."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def head(self) -> tuple[int, str] | None:
        row = self._db.fetchone("SELECT seq, event_hash FROM audit_chain ORDER BY seq DESC LIMIT 1")
        return (int(row[0]), str(row[1])) if row else None

    def append(self, event_row: dict[str, Any]) -> str:
        with self._db.transaction():
            head = self.head()
            previous = head[1] if head else GENESIS_HASH
            payload = payload_hash(event_row)
            digest = link_hash(event_row["seq"], event_row["event_id"], payload, previous)
            self._db.execute("INSERT INTO audit_chain(seq, event_id, payload_hash, previous_hash, event_hash, "
                             "chained_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (event_row["seq"], event_row["event_id"], payload, previous, digest,
                              event_row["created_at"]))
        return digest

    def links(self, after_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_chain WHERE seq > ? ORDER BY seq" + (" LIMIT ?" if limit else "")
        return [dict(zip(r.keys(), tuple(r))) for r in self._db.fetchall(sql, (after_seq, limit) if limit else (after_seq,))]

    def event_rows(self, after_seq: int = 0) -> list[dict[str, Any]]:
        cols = ", ".join(EVENT_COLUMNS)
        return [dict(zip(EVENT_COLUMNS, tuple(r))) for r in
                self._db.fetchall(f"SELECT {cols} FROM events WHERE seq > ? ORDER BY seq", (after_seq,))]

    def link_at(self, seq: int) -> dict[str, Any] | None:
        r = self._db.fetchone("SELECT * FROM audit_chain WHERE seq = ?", (seq,))
        return dict(zip(r.keys(), tuple(r))) if r else None


class EventRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def next_seq(self) -> int:
        (seq,) = self._db.fetchone("SELECT COALESCE(MAX(seq), 0) + 1 FROM events")
        return int(seq)

    def insert(self, e: WorkflowEvent) -> None:
        """Append one event and its audit-chain link in the same transaction (Stage 7)."""
        values = (e.event_id, e.seq, e.workflow_id, e.event_type.value, e.agent_id, e.actor_type.value, e.action_id,
                  e.tool, e.resource_id, e.resource_version_before, e.resource_version_after, e.approval_id,
                  e.decision, e.reason_code, _json(e.parent_event_ids), _json(e.related_event_ids), e.summary,
                  _json(e.details), e.created_at.isoformat())
        with self._db.transaction():
            self._db.execute(
                "INSERT INTO events(event_id, seq, workflow_id, event_type, agent_id, actor_type, action_id, tool, "
                "resource_id, resource_version_before, resource_version_after, approval_id, decision, reason_code, "
                "parent_event_ids_json, related_event_ids_json, summary, details_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            AuditChainRepository(self._db).append(dict(zip(EVENT_COLUMNS, values)))

    def query(
        self,
        *,
        workflow_id: str | None = None,
        agent_id: str | None = None,
        resource_id: str | None = None,
        action_id: str | None = None,
        approval_id: str | None = None,
        event_type: EventType | None = None,
    ) -> list[WorkflowEvent]:
        clauses, params = [], []
        for column, value in (("workflow_id", workflow_id), ("agent_id", agent_id), ("resource_id", resource_id),
                              ("action_id", action_id), ("approval_id", approval_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return [self._from_row(r) for r in self._db.fetchall(f"SELECT * FROM events{where} ORDER BY seq", params)]

    def get(self, event_id: str) -> WorkflowEvent | None:
        row = self._db.fetchone("SELECT * FROM events WHERE event_id = ?", (event_id,))
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row: Any) -> WorkflowEvent:
        return WorkflowEvent(
            event_id=row["event_id"], seq=row["seq"], workflow_id=row["workflow_id"],
            event_type=EventType(row["event_type"]), agent_id=row["agent_id"], actor_type=ActorType(row["actor_type"]),
            action_id=row["action_id"], tool=row["tool"], resource_id=row["resource_id"],
            resource_version_before=row["resource_version_before"], resource_version_after=row["resource_version_after"],
            approval_id=row["approval_id"], decision=row["decision"], reason_code=row["reason_code"],
            parent_event_ids=json.loads(row["parent_event_ids_json"]),
            related_event_ids=json.loads(row["related_event_ids_json"]), summary=row["summary"],
            details=json.loads(row["details_json"]), created_at=datetime.fromisoformat(row["created_at"]),
        )


class ApprovalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, r: ApprovalRecord) -> None:
        self._db.execute(
            "INSERT INTO approvals(approval_id, workflow_id, operation, resource_id, resource_version, "
            "approved_parameters_json, operation_hash, requested_by, requesting_action_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r.approval_id, r.workflow_id, r.operation, r.resource_id, r.resource_version,
             _json(r.approved_parameters), r.operation_hash, r.requested_by, r.requesting_action_id,
             r.status.value, r.created_at.isoformat()),
        )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        row = self._db.fetchone("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
        return self._from_row(row) if row else None

    def list(self, *, workflow_id: str | None = None, resource_id: str | None = None,
             statuses: tuple[ApprovalStatus, ...] = ()) -> list[ApprovalRecord]:
        clauses, params = [], []
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if resource_id is not None:
            clauses.append("resource_id = ?")
            params.append(resource_id)
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(s.value for s in statuses)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return [self._from_row(r) for r in self._db.fetchall(f"SELECT * FROM approvals{where} ORDER BY approval_id", params)]

    def grant(self, approval_id: str, *, approver_id: str, decided_at: datetime, expires_at: datetime,
              binding_hash: str, signature: str) -> bool:
        cursor = self._db.execute(
            "UPDATE approvals SET status = ?, approver_id = ?, decided_at = ?, expires_at = ?, binding_hash = ?, "
            "signature = ?, status_reason = 'approved by human operator' WHERE approval_id = ? AND status = ?",
            (ApprovalStatus.APPROVED.value, approver_id, decided_at.isoformat(), expires_at.isoformat(), binding_hash,
             signature, approval_id, ApprovalStatus.PENDING.value),
        )
        return cursor.rowcount == 1

    def transition(self, approval_id: str, *, from_statuses: tuple[ApprovalStatus, ...], to: ApprovalStatus,
                   reason: str, approver_id: str | None = None, decided_at: datetime | None = None,
                   consumed_by_action_id: str | None = None) -> bool:
        """Conditional status change; returns False if the row was not in ``from_statuses``."""
        sets, params = ["status = ?", "status_reason = ?"], [to.value, reason]
        for column, value in (("approver_id", approver_id), ("consumed_by_action_id", consumed_by_action_id)):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if decided_at is not None:
            sets.append("decided_at = ?")
            params.append(decided_at.isoformat())
        params.append(approval_id)
        params.extend(s.value for s in from_statuses)
        cursor = self._db.execute(
            f"UPDATE approvals SET {', '.join(sets)} WHERE approval_id = ? "
            f"AND status IN ({','.join('?' * len(from_statuses))})",
            params,
        )
        return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: Any) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row["approval_id"], workflow_id=row["workflow_id"], operation=row["operation"],
            resource_id=row["resource_id"], resource_version=row["resource_version"],
            approved_parameters=json.loads(row["approved_parameters_json"]), operation_hash=row["operation_hash"],
            binding_hash=row["binding_hash"], signature=row["signature"], requested_by=row["requested_by"],
            requesting_action_id=row["requesting_action_id"], status=ApprovalStatus(row["status"]),
            approver_id=row["approver_id"], status_reason=row["status_reason"],
            created_at=datetime.fromisoformat(row["created_at"]), decided_at=_dt(row["decided_at"]),
            expires_at=_dt(row["expires_at"]), consumed_by_action_id=row["consumed_by_action_id"],
        )


class SentEmailRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, *, email_id: str, action_id: str, workflow_id: str, approval_id: str, resource_id: str,
               resource_version: int, recipient: str, subject: str, body: str, status: str, sent_at: datetime) -> None:
        self._db.execute(
            "INSERT INTO sent_emails(email_id, action_id, workflow_id, approval_id, resource_id, resource_version, "
            "recipient, subject, body, status, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (email_id, action_id, workflow_id, approval_id, resource_id, resource_version, recipient, subject, body,
             status, sent_at.isoformat()),
        )

    def list(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sent_emails" + (" WHERE workflow_id = ?" if workflow_id else "") + " ORDER BY email_id"
        return [dict(r) for r in self._db.fetchall(sql, (workflow_id,) if workflow_id else ())]


class EthicsEvaluationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, r: CouncilEvaluationRecord) -> None:
        self._db.execute(
            "INSERT INTO ethics_evaluations(evaluation_id, workflow_id, action_id, council_type, experiment_id, "
            "snapshot_digest, report_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.evaluation_id, r.workflow_id, r.action_id, r.council_type, r.experiment_id, r.snapshot_digest,
             r.report.model_dump_json(), r.created_at.isoformat()),
        )

    def for_workflow(self, workflow_id: str) -> list[CouncilEvaluationRecord]:
        try:
            rows = self._db.fetchall(
                "SELECT * FROM ethics_evaluations WHERE workflow_id = ? ORDER BY evaluation_id", (workflow_id,))
        except OPERATIONAL_ERRORS:  # database created before Stage 3 (no table): nothing recorded
            return []
        return [CouncilEvaluationRecord(
            evaluation_id=x["evaluation_id"], workflow_id=x["workflow_id"], action_id=x["action_id"],
            council_type=x["council_type"], experiment_id=x["experiment_id"], snapshot_digest=x["snapshot_digest"],
            report=CouncilReport.model_validate_json(x["report_json"]),
            created_at=datetime.fromisoformat(x["created_at"])) for x in rows]


def _safe_rows(db: Database, sql: str, params: tuple = ()) -> list[Any]:
    try:
        return db.fetchall(sql, params)
    except OPERATIONAL_ERRORS:  # database created before the table existed
        return []


class MessageRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, m: AgentMessage) -> None:
        self._db.execute(
            "INSERT INTO agent_messages(message_id, workflow_id, sender_id, recipient_id, message_type, text, "
            "parent_message_id, action_id, event_id, is_system, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (m.message_id, m.workflow_id, m.sender_id, m.recipient_id, m.message_type.value, m.text,
             m.parent_message_id, m.action_id, m.event_id, int(m.is_system), m.created_at.isoformat()),
        )

    @staticmethod
    def _from_row(r: Any) -> AgentMessage:
        return AgentMessage(message_id=r["message_id"], workflow_id=r["workflow_id"], sender_id=r["sender_id"],
                            recipient_id=r["recipient_id"], message_type=MessageType(r["message_type"]), text=r["text"],
                            parent_message_id=r["parent_message_id"], action_id=r["action_id"], event_id=r["event_id"],
                            is_system=bool(r["is_system"]), created_at=datetime.fromisoformat(r["created_at"]))

    def get(self, message_id: str) -> AgentMessage | None:
        rows = _safe_rows(self._db, "SELECT * FROM agent_messages WHERE message_id = ?", (message_id,))
        return self._from_row(rows[0]) if rows else None

    def for_workflow(self, workflow_id: str) -> list[AgentMessage]:
        return [self._from_row(r) for r in _safe_rows(
            self._db, "SELECT * FROM agent_messages WHERE workflow_id = ? ORDER BY created_at, message_id",
            (workflow_id,))]

    def inbox(self, workflow_id: str, recipient_id: str) -> list[AgentMessage]:
        return [self._from_row(r) for r in _safe_rows(
            self._db, "SELECT * FROM agent_messages WHERE workflow_id = ? AND recipient_id = ? "
            "ORDER BY created_at, message_id", (workflow_id, recipient_id))]


class JsonRecordRepository:
    """Append-only JSON records keyed by workflow (decisions, interventions, assessments, memory)."""

    _INSERT = {
        "agent_decisions": "INSERT INTO agent_decisions(decision_record_id, workflow_id, agent_id, step, record_json, "
                           "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        "interventions": "INSERT INTO interventions(intervention_id, workflow_id, record_json, created_at) "
                         "VALUES (?, ?, ?, ?)",
        "autonomous_assessments": "INSERT INTO autonomous_assessments(assessment_id, workflow_id, action_id, monitor, "
                                  "record_json) VALUES (?, ?, ?, ?, ?)",
        "agent_memory": "INSERT INTO agent_memory(entry_id, workflow_id, agent_id, kind, content_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
    }
    _SELECT = {
        "agent_decisions": "SELECT record_json FROM agent_decisions WHERE workflow_id = ? ORDER BY step, decision_record_id",
        "interventions": "SELECT record_json FROM interventions WHERE workflow_id = ? ORDER BY created_at, intervention_id",
        "autonomous_assessments": "SELECT record_json FROM autonomous_assessments WHERE workflow_id = ? "
                                  "ORDER BY assessment_id",
        "agent_memory": "SELECT content_json AS record_json FROM agent_memory WHERE workflow_id = ? ORDER BY entry_id",
    }

    def __init__(self, db: Database, table: str) -> None:
        if table not in self._INSERT:
            raise ValueError(table)
        self._db, self._table = db, table

    def insert(self, *values: Any) -> None:
        self._db.execute(self._INSERT[self._table], values)

    def for_workflow(self, workflow_id: str) -> list[dict[str, Any]]:
        return [json.loads(r["record_json"]) for r in _safe_rows(self._db, self._SELECT[self._table], (workflow_id,))]

    def memory_for(self, workflow_id: str, agent_id: str) -> list[dict[str, Any]]:
        assert self._table == "agent_memory"
        return [json.loads(r["content_json"]) for r in _safe_rows(
            self._db, "SELECT content_json FROM agent_memory WHERE workflow_id = ? AND agent_id = ? ORDER BY entry_id",
            (workflow_id, agent_id))]


class AgentRegistryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, agent_id: str, role: str, display_name: str, is_adversarial: bool) -> None:
        self._db.execute(
            "INSERT INTO agent_registry(agent_id, role, display_name, is_adversarial) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET role = excluded.role, display_name = excluded.display_name, "
            "is_adversarial = excluded.is_adversarial", (agent_id, role, display_name, int(is_adversarial)))

    def list(self) -> list[dict[str, Any]]:
        return [{"agent_id": r["agent_id"], "role": r["role"], "display_name": r["display_name"],
                 "is_adversarial": bool(r["is_adversarial"])}
                for r in _safe_rows(self._db, "SELECT * FROM agent_registry ORDER BY agent_id")]

    def decisions_by_agent(self, agent_id: str) -> list[dict[str, Any]]:
        return [json.loads(r["record_json"]) for r in _safe_rows(
            self._db, "SELECT record_json FROM agent_decisions WHERE agent_id = ? ORDER BY workflow_id, step",
            (agent_id,))]


class Repositories:
    """Bundle of repositories over one database."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.workflows = WorkflowRepository(db)
        self.resources = ResourceRepository(db)
        self.actions = ActionRepository(db)
        self.decisions = DecisionRepository(db)
        self.events = EventRepository(db)
        self.approvals = ApprovalRepository(db)
        self.sent_emails = SentEmailRepository(db)
        self.ethics = EthicsEvaluationRepository(db)
        self.messages = MessageRepository(db)
        self.agent_decisions = JsonRecordRepository(db, "agent_decisions")
        self.interventions = JsonRecordRepository(db, "interventions")
        self.autonomous_assessments = JsonRecordRepository(db, "autonomous_assessments")
        self.agent_memory = JsonRecordRepository(db, "agent_memory")
        self.agent_registry = AgentRegistryRepository(db)


class ConsoleJobRepository:
    """Trusted job state, versioned with immutable snapshots and a CAS update.

    Progress is control metadata, separate from agent-editable resources. Each snapshot
    includes the responsible control principal. Recovery never schedules a job.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, job_id: str, record: dict[str, Any], actor: str) -> None:
        with self._db.transaction():
            self._db.execute("INSERT INTO console_jobs(job_id, version, record_json) VALUES (?, 1, ?)",
                             (job_id, _json(record)))
            self._db.execute("INSERT INTO console_job_versions(job_id, version, actor, record_json) VALUES (?, 1, ?, ?)",
                             (job_id, actor, _json(record)))

    def save(self, job_id: str, record: dict[str, Any], *, expected_version: int, actor: str) -> int:
        with self._db.transaction():
            changed = self._db.execute(
                "UPDATE console_jobs SET version = version + 1, record_json = ? WHERE job_id = ? AND version = ?",
                (_json(record), job_id, expected_version))
            if changed.rowcount != 1:
                raise RuntimeError("job version conflict")
            self._db.execute("INSERT INTO console_job_versions(job_id, version, actor, record_json) VALUES (?, ?, ?, ?)",
                             (job_id, expected_version + 1, actor, _json(record)))
        return expected_version + 1

    def get(self, job_id: str) -> tuple[dict[str, Any], int] | None:
        row = self._db.fetchone("SELECT record_json, version FROM console_jobs WHERE job_id = ?", (job_id,))
        return (json.loads(row["record_json"]), row["version"]) if row else None

    def list(self) -> list[dict[str, Any]]:
        return [json.loads(r["record_json"]) for r in self._db.fetchall(
            "SELECT record_json FROM console_jobs ORDER BY job_id DESC")]


class ConsoleReadRepository:
    """Bounded read queries, used with a mode=ro connection. No SQL comes from HTTP inputs."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def events(self, *, workflow_id: str | None = None, agent_id: str | None = None,
               event_type: str | None = None, search: str = "", after_seq: int = 0,
               limit: int = 100, offset: int = 0) -> list[WorkflowEvent]:
        rows = self._db.fetchall(
            "SELECT * FROM events WHERE seq > ? AND (? IS NULL OR workflow_id = ?) "
            "AND (? IS NULL OR agent_id = ?) AND (? IS NULL OR event_type = ?) "
            "AND (? = '' OR instr(lower(summary), lower(?)) > 0 OR instr(lower(event_id), lower(?)) > 0) "
            "ORDER BY seq LIMIT ? OFFSET ?",
            (after_seq, workflow_id, workflow_id, agent_id, agent_id, event_type, event_type,
             search, search, search, limit, offset))
        return [EventRepository._from_row(r) for r in rows]


class ResearchRepository:
    """Trusted, CAS-versioned run records and append-only candidate trajectories."""
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, record: dict[str, Any], actor: str, occurred_at: str) -> None:
        with self.db.transaction():
            self.db.execute("INSERT INTO research_runs VALUES (?, 1, ?)", (record["run_id"], _json(record)))
            self.db.execute("INSERT INTO research_run_versions VALUES (?, 1, ?, ?)", (record["run_id"], actor, _json(record)))
            self.audit(record["run_id"], actor, occurred_at, "CREATED", 1)

    def audit(self, run_id: str, actor: str, occurred_at: str, operation: str, version: int) -> None:
        self.db.execute("INSERT INTO research_audit VALUES (?, ?, ?, ?, ?, ?)",
                        (self.db.next_id("research_event"), run_id, actor, occurred_at, operation, version))

    def save(self, record: dict[str, Any], *, expected_version: int, actor: str, occurred_at: str) -> int:
        with self.db.transaction():
            result = self.db.execute("UPDATE research_runs SET record_json = ?, version = version + 1 WHERE run_id = ? AND version = ?",
                                     (_json(record), record["run_id"], expected_version))
            if result.rowcount != 1:
                raise RuntimeError("research run version conflict")
            self.db.execute("INSERT INTO research_run_versions VALUES (?, ?, ?, ?)",
                            (record["run_id"], expected_version + 1, actor, _json(record)))
            self.audit(record["run_id"], actor, occurred_at, record["status"], expected_version + 1)
        return expected_version + 1

    def get(self, run_id: str) -> tuple[dict[str, Any], int] | None:
        row = self.db.fetchone("SELECT record_json, version FROM research_runs WHERE run_id = ?", (run_id,))
        return (json.loads(row["record_json"]), row["version"]) if row else None

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return [json.loads(r["record_json"]) for r in self.db.fetchall(
            "SELECT record_json FROM research_runs ORDER BY run_id DESC LIMIT ? OFFSET ?", (limit, offset))]

    def add_candidate(self, run_id: str, candidate_id: str, record: dict[str, Any]) -> None:
        with self.db.transaction():
            self.db.execute("INSERT INTO research_candidates VALUES (?, ?, ?)", (run_id, candidate_id, _json(record)))
            self.audit(run_id, "swarmguard.stage5", record["recorded_at"], "CANDIDATE_RECORDED", 1)

    def candidates(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return [json.loads(r["record_json"]) for r in self.db.fetchall(
            "SELECT record_json FROM research_candidates WHERE run_id = ? ORDER BY candidate_id LIMIT ?", (run_id, limit))]


class PlatformRepository:
    """Stage 6 trusted metadata, immutable baselines and versioned control state."""
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, kind: str, record_id: str, record: dict[str, Any], *, actor: str, occurred_at: str) -> None:
        with self.db.transaction():
            self.db.execute("INSERT INTO platform_records VALUES (?, ?, 1, ?)", (kind, record_id, _json(record)))
            self.db.execute("INSERT INTO platform_versions VALUES (?, ?, 1, ?, ?, ?)",
                            (kind, record_id, actor, occurred_at, _json(record)))

    def get(self, kind: str, record_id: str) -> tuple[dict[str, Any], int] | None:
        r = self.db.fetchone("SELECT record_json, version FROM platform_records WHERE kind = ? AND record_id = ?", (kind, record_id))
        return (json.loads(r["record_json"]), r["version"]) if r else None

    def list(self, kind: str, limit: int = 100) -> list[dict[str, Any]]:
        return [json.loads(r["record_json"]) for r in self.db.fetchall(
            "SELECT record_json FROM platform_records WHERE kind = ? ORDER BY record_id DESC LIMIT ?", (kind, limit))]

    def save(self, kind: str, record_id: str, record: dict[str, Any], *, expected_version: int,
             actor: str, occurred_at: str) -> int:
        if kind == "baseline":
            raise ValueError("official baselines are immutable; designate a new baseline")
        with self.db.transaction():
            changed = self.db.execute("UPDATE platform_records SET record_json = ?, version = version + 1 "
                "WHERE kind = ? AND record_id = ? AND version = ?", (_json(record), kind, record_id, expected_version))
            if changed.rowcount != 1:
                raise RuntimeError("platform record version conflict")
            self.db.execute("INSERT INTO platform_versions VALUES (?, ?, ?, ?, ?, ?)",
                (kind, record_id, expected_version + 1, actor, occurred_at, _json(record)))
        return expected_version + 1

    def append_integration_event(self, event: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO integration_events(event_id, integration_id, external_event_id, record_json) VALUES (?, ?, ?, ?)",
            (event["event_id"], event["integration_id"], event.get("external_event_id"), _json(event)))

    def integration_events(self, integration_id: str | None = None, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return [{**json.loads(r["record_json"]), "seq": r["seq"]} for r in self.db.fetchall(
            "SELECT seq, record_json FROM integration_events WHERE (? IS NULL OR integration_id = ?) "
            "AND seq > ? ORDER BY seq LIMIT ?", (integration_id, integration_id, after_seq, limit))]


# ---------------------------------------------------------------------------- Stage 7 trusted state


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(zip(row.keys(), tuple(row)))


class ServiceIdentityRepository:
    """Agent-runtime service identities, agent bindings and revoked credential IDs (Boundary B)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_runtime(self, runtime_id: str, description: str, *, created_by: str, at: str) -> None:
        self._db.execute("INSERT INTO service_runtimes(runtime_id, description, status, created_at, created_by) "
                         "VALUES (?, ?, 'ACTIVE', ?, ?)", (runtime_id, description, at, created_by))

    def runtime(self, runtime_id: str) -> dict[str, Any] | None:
        row = self._db.fetchone("SELECT * FROM service_runtimes WHERE runtime_id = ?", (runtime_id,))
        return _row_dict(row) if row else None

    def runtimes(self) -> list[dict[str, Any]]:
        return [_row_dict(r) for r in self._db.fetchall("SELECT * FROM service_runtimes ORDER BY runtime_id")]

    def revoke_runtime(self, runtime_id: str, *, by: str, reason: str, at: str) -> bool:
        with self._db.transaction():
            cur = self._db.execute("UPDATE service_runtimes SET status = 'REVOKED', revoked_at = ?, revoked_by = ?, "
                                   "revocation_reason = ? WHERE runtime_id = ? AND status = 'ACTIVE'",
                                   (at, by, reason, runtime_id))
            self._db.execute("UPDATE agent_bindings SET status = 'REVOKED', revoked_at = ?, revoked_by = ? "
                             "WHERE runtime_id = ? AND status = 'ACTIVE'", (at, by, runtime_id))
            return cur.rowcount == 1

    def bind_agent(self, agent_id: str, runtime_id: str, role: str, workflows: list[str], capabilities: list[str],
                   *, created_by: str, at: str) -> None:
        self._db.execute("INSERT INTO agent_bindings(agent_id, runtime_id, role, workflows_json, capabilities_json, "
                         "status, created_at, created_by) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                         (agent_id, runtime_id, role, _json(sorted(workflows)), _json(sorted(capabilities)), at,
                          created_by))

    def binding(self, agent_id: str) -> dict[str, Any] | None:
        row = self._db.fetchone("SELECT * FROM agent_bindings WHERE agent_id = ?", (agent_id,))
        return self._binding(row) if row else None

    def bindings(self) -> list[dict[str, Any]]:
        return [self._binding(r) for r in self._db.fetchall("SELECT * FROM agent_bindings ORDER BY agent_id")]

    @staticmethod
    def _binding(row: Any) -> dict[str, Any]:
        d = _row_dict(row)
        d["workflows"] = json.loads(d.pop("workflows_json"))
        d["capabilities"] = json.loads(d.pop("capabilities_json"))
        return d

    def revoke_agent(self, agent_id: str, *, by: str, at: str) -> bool:
        cur = self._db.execute("UPDATE agent_bindings SET status = 'REVOKED', revoked_at = ?, revoked_by = ? "
                               "WHERE agent_id = ? AND status = 'ACTIVE'", (at, by, agent_id))
        return cur.rowcount == 1

    def revoke_credential(self, jti: str, *, by: str, reason: str, at: str) -> bool:
        try:
            self._db.execute("INSERT INTO revoked_credentials(jti, revoked_at, revoked_by, reason) VALUES (?, ?, ?, ?)",
                             (jti, at, by, reason))
            return True
        except INTEGRITY_ERRORS:
            return False

    def credential_revoked(self, jti: str) -> bool:
        return self._db.fetchone("SELECT 1 FROM revoked_credentials WHERE jti = ?", (jti,)) is not None

    def revoked_credentials(self) -> list[dict[str, Any]]:
        return [_row_dict(r) for r in self._db.fetchall("SELECT * FROM revoked_credentials ORDER BY revoked_at")]


OPERATION_STATES = ("PENDING", "AUTHORIZED", "EXECUTING", "SUCCEEDED", "FAILED", "BLOCKED", "UNKNOWN")
FINAL_OPERATION_STATES = frozenset({"SUCCEEDED", "FAILED", "BLOCKED", "UNKNOWN"})


class ProtectedOperationRepository:
    """Durable remote-operation state with compare-and-set transitions (Boundary B).

    ``UNIQUE(agent_id, request_id)`` is the idempotency key; ``UNIQUE(action_id)`` binds one gateway
    action to at most one operation. Every transition is versioned in ``operation_transitions``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, *, operation_id: str, agent_id: str, runtime_id: str, request_id: str, request_digest: str,
               workflow_id: str, tool: str, correlation_id: str, at: str) -> bool:
        try:
            with self._db.transaction():
                self._db.execute(
                    "INSERT INTO protected_operations(operation_id, agent_id, runtime_id, request_id, request_digest, "
                    "workflow_id, tool, state, correlation_id, version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, 1, ?, ?)",
                    (operation_id, agent_id, runtime_id, request_id, request_digest, workflow_id, tool, correlation_id,
                     at, at))
                self._db.execute("INSERT INTO operation_transitions(operation_id, version, from_state, to_state, actor, "
                                 "reason_code, occurred_at) VALUES (?, 1, NULL, 'PENDING', ?, NULL, ?)",
                                 (operation_id, "swarmguard.gateway", at))
            return True
        except INTEGRITY_ERRORS:
            return False

    def get(self, operation_id: str) -> dict[str, Any] | None:
        row = self._db.fetchone("SELECT * FROM protected_operations WHERE operation_id = ?", (operation_id,))
        return self._op(row) if row else None

    def by_request(self, agent_id: str, request_id: str) -> dict[str, Any] | None:
        row = self._db.fetchone("SELECT * FROM protected_operations WHERE agent_id = ? AND request_id = ?",
                                (agent_id, request_id))
        return self._op(row) if row else None

    def list(self, *, state: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if state:
            rows = self._db.fetchall("SELECT * FROM protected_operations WHERE state = ? ORDER BY created_at DESC, "
                                     "operation_id DESC LIMIT ?", (state, limit))
        else:
            rows = self._db.fetchall("SELECT * FROM protected_operations ORDER BY created_at DESC, operation_id DESC "
                                     "LIMIT ?", (limit,))
        return [self._op(r) for r in rows]

    def counts(self) -> dict[str, int]:
        return {str(r[0]): int(r[1]) for r in
                self._db.fetchall("SELECT state, COUNT(*) FROM protected_operations GROUP BY state")}

    def transitions(self, operation_id: str) -> list[dict[str, Any]]:
        return [_row_dict(r) for r in self._db.fetchall(
            "SELECT * FROM operation_transitions WHERE operation_id = ? ORDER BY version", (operation_id,))]

    @staticmethod
    def _op(row: Any) -> dict[str, Any]:
        d = _row_dict(row)
        d["expected"] = json.loads(d.pop("expected_json") or "{}")
        for key in ("response_json", "result_json"):
            raw = d.pop(key)
            d[key[:-5]] = json.loads(raw) if raw else None
        return d

    def transition(self, operation_id: str, *, from_states: tuple[str, ...], to_state: str, actor: str, at: str,
                   reason_code: str | None = None, **fields: Any) -> bool:
        """Conditional state change; returns False if another writer moved the operation first."""
        if to_state not in OPERATION_STATES:
            raise ValueError(f"unknown operation state {to_state}")
        allowed = {"action_id", "authorization_digest", "expected", "response", "result", "grant_expires_at"}
        if set(fields) - allowed:
            raise ValueError(f"unsupported operation fields: {sorted(set(fields) - allowed)}")
        sets, params = ["state = ?", "reason_code = COALESCE(?, reason_code)", "version = version + 1", "updated_at = ?"], \
            [to_state, reason_code, at]
        for key, value in fields.items():
            column = {"expected": "expected_json", "response": "response_json", "result": "result_json"}.get(key, key)
            sets.append(f"{column} = ?")
            params.append(_json(value) if key in ("expected", "response", "result") else value)
        marks = ", ".join("?" for _ in from_states)
        with self._db.transaction():
            current = self._db.fetchone("SELECT state, version FROM protected_operations WHERE operation_id = ?",
                                        (operation_id,))
            if current is None or current[0] not in from_states:
                return False
            # nosec B608: column names come only from the fixed allowlist above; every value is a bound parameter.
            cur = self._db.execute(f"UPDATE protected_operations SET {', '.join(sets)} WHERE operation_id = ? "  # nosec B608
                                   f"AND state IN ({marks}) AND version = ?",
                                   (*params, operation_id, *from_states, current[1]))
            if cur.rowcount != 1:
                return False
            self._db.execute("INSERT INTO operation_transitions(operation_id, version, from_state, to_state, actor, "
                             "reason_code, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (operation_id, int(current[1]) + 1, current[0], to_state, actor, reason_code, at))
        return True


class ControlStateRepository:
    """Versioned trusted control flags (emergency stop, disabled capabilities). CAS writes only."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> tuple[dict[str, Any], int] | None:
        row = self._db.fetchone("SELECT value_json, version FROM control_state WHERE key = ?", (key,))
        return (json.loads(row[0]), int(row[1])) if row else None

    def put(self, key: str, value: dict[str, Any], *, expected_version: int | None, by: str, at: str) -> int:
        with self._db.transaction():
            current = self.get(key)
            if (current[1] if current else None) != expected_version:
                raise ValueError(f"control state {key} changed concurrently")
            if current is None:
                self._db.execute("INSERT INTO control_state(key, value_json, version, updated_at, updated_by) "
                                 "VALUES (?, ?, 1, ?, ?)", (key, _json(value), at, by))
                return 1
            self._db.execute("UPDATE control_state SET value_json = ?, version = version + 1, updated_at = ?, "
                             "updated_by = ? WHERE key = ? AND version = ?", (_json(value), at, by, key, current[1]))
            return current[1] + 1


class HeartbeatRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def beat(self, service: str, status: str, detail: dict[str, Any], at: str) -> None:
        self._db.execute("INSERT INTO service_heartbeats(service, status, detail_json, heartbeat_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(service) DO UPDATE SET status = excluded.status, "
                         "detail_json = excluded.detail_json, heartbeat_at = excluded.heartbeat_at",
                         (service, status, _json(detail), at))

    def all(self) -> list[dict[str, Any]]:
        out = []
        for r in self._db.fetchall("SELECT * FROM service_heartbeats ORDER BY service"):
            d = _row_dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            out.append(d)
        return out
