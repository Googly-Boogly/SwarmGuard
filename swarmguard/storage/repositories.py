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
import sqlite3

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
from swarmguard.storage.database import Database


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


class EventRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def next_seq(self) -> int:
        (seq,) = self._db.fetchone("SELECT COALESCE(MAX(seq), 0) + 1 FROM events")
        return int(seq)

    def insert(self, e: WorkflowEvent) -> None:
        self._db.execute(
            "INSERT INTO events(event_id, seq, workflow_id, event_type, agent_id, actor_type, action_id, tool, "
            "resource_id, resource_version_before, resource_version_after, approval_id, decision, reason_code, "
            "parent_event_ids_json, related_event_ids_json, summary, details_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e.event_id, e.seq, e.workflow_id, e.event_type.value, e.agent_id, e.actor_type.value, e.action_id,
             e.tool, e.resource_id, e.resource_version_before, e.resource_version_after, e.approval_id, e.decision,
             e.reason_code, _json(e.parent_event_ids), _json(e.related_event_ids), e.summary, _json(e.details),
             e.created_at.isoformat()),
        )

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
        except sqlite3.OperationalError:  # database created before Stage 3 (no table): nothing recorded
            return []
        return [CouncilEvaluationRecord(
            evaluation_id=x["evaluation_id"], workflow_id=x["workflow_id"], action_id=x["action_id"],
            council_type=x["council_type"], experiment_id=x["experiment_id"], snapshot_digest=x["snapshot_digest"],
            report=CouncilReport.model_validate_json(x["report_json"]),
            created_at=datetime.fromisoformat(x["created_at"])) for x in rows]


def _safe_rows(db: Database, sql: str, params: tuple = ()) -> list[Any]:
    try:
        return db.fetchall(sql, params)
    except sqlite3.OperationalError:  # database created before the table existed
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
