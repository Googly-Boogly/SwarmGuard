"""SQLite persistence.

Only trusted components (monitor, workspace, approval manager, tool executor)
hold a ``Database`` reference. Agents never do.

The schema is split into three trust boundaries (see docs/THREAT_MODEL.md):
A = agent-editable shared workspace, B = trusted security state,
C = trusted audit state. This is a trusted *local simulation log*: append-only
by convention, NOT tamper-proof (anyone with filesystem access can edit it).

Agents never hold a ``Database`` reference. All SQL lives in this module and in
``repositories.py``; no endpoint or agent can submit SQL.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

-- Boundary B: trusted security state -------------------------------------
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_version INTEGER NOT NULL,
    approved_parameters_json TEXT NOT NULL,
    operation_hash TEXT NOT NULL,
    binding_hash TEXT,
    signature TEXT,
    requested_by TEXT NOT NULL,
    requesting_action_id TEXT,
    status TEXT NOT NULL,
    approver_id TEXT,
    status_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    expires_at TEXT,
    consumed_by_action_id TEXT UNIQUE
);

-- Boundary A: agent-editable shared workspace ------------------------------
CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    workflow_id TEXT,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    acl_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Boundary C: trusted audit state ------------------------------------------
CREATE TABLE IF NOT EXISTS resource_versions (
    resource_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    changes_json TEXT NOT NULL DEFAULT '{}',
    agent_id TEXT NOT NULL,
    action_id TEXT,
    event_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (resource_id, version)
);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    resource_id TEXT,
    resource_version INTEGER,
    parent_action_ids_json TEXT NOT NULL DEFAULT '[]',
    approval_id TEXT,
    request_id TEXT,
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_workflow ON actions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_actions_request ON actions(agent_id, request_id);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    record_json TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_workflow ON decisions(workflow_id);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    action_id TEXT,
    tool TEXT,
    resource_id TEXT,
    resource_version_before INTEGER,
    resource_version_after INTEGER,
    approval_id TEXT,
    decision TEXT,
    reason_code TEXT,
    parent_event_ids_json TEXT NOT NULL DEFAULT '[]',
    related_event_ids_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_workflow ON events(workflow_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_resource ON events(resource_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_action ON events(action_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_approval ON events(approval_id, seq);

CREATE TABLE IF NOT EXISTS sent_emails (
    email_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    approval_id TEXT NOT NULL UNIQUE,
    resource_id TEXT NOT NULL,
    resource_version INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

-- Stage 3 (Boundary C): advisory ethics-council results recorded by trusted harness code.
-- Written via WorkflowMonitor.record_council_evaluation only; never by monitors or agents.
CREATE TABLE IF NOT EXISTS ethics_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    council_type TEXT NOT NULL,
    experiment_id TEXT,
    snapshot_digest TEXT,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ethics_workflow ON ethics_evaluations(workflow_id, evaluation_id);

-- Stage 4 (Boundary C unless noted): autonomous-runtime audit records, written only by trusted code.
CREATE TABLE IF NOT EXISTS agent_messages (
    message_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    text TEXT NOT NULL,
    parent_message_id TEXT,
    action_id TEXT,
    event_id TEXT,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_workflow ON agent_messages(workflow_id, message_id);
CREATE TABLE IF NOT EXISTS agent_decisions (
    decision_record_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_workflow ON agent_decisions(workflow_id, step);
CREATE TABLE IF NOT EXISTS interventions (
    intervention_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomous_assessments (
    assessment_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    monitor TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_assess_workflow ON autonomous_assessments(workflow_id, assessment_id);
-- private agent working memory (readable only by its owner through the runtime)
CREATE TABLE IF NOT EXISTS agent_memory (
    entry_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_agent ON agent_memory(workflow_id, agent_id, entry_id);
-- trusted registry snapshot (identity + role), written by the simulator at registration
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_adversarial INTEGER NOT NULL DEFAULT 0
);
-- Stage 4.5 trusted control state. Sessions and credentials are never stored here.
CREATE TABLE IF NOT EXISTS console_jobs (
    job_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS console_job_versions (
    job_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    actor TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (job_id, version)
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY, version INTEGER NOT NULL, record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_run_versions (
    run_id TEXT NOT NULL, version INTEGER NOT NULL, actor TEXT NOT NULL,
    record_json TEXT NOT NULL, PRIMARY KEY(run_id, version)
);
CREATE TABLE IF NOT EXISTS research_candidates (
    run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, record_json TEXT NOT NULL,
    PRIMARY KEY(run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS research_audit (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL, operation TEXT NOT NULL, version INTEGER NOT NULL
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS platform_records (
    kind TEXT NOT NULL, record_id TEXT NOT NULL, version INTEGER NOT NULL, record_json TEXT NOT NULL,
    PRIMARY KEY(kind, record_id)
);
CREATE TABLE IF NOT EXISTS platform_versions (
    kind TEXT NOT NULL, record_id TEXT NOT NULL, version INTEGER NOT NULL, actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY(kind, record_id, version)
);
CREATE TABLE IF NOT EXISTS integration_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, integration_id TEXT NOT NULL,
    external_event_id TEXT, record_json TEXT NOT NULL, UNIQUE(integration_id, external_event_id)
);
"""
SCHEMA_VERSION = "6"


class Database:
    """Thin, thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path: str | Path = ":memory:", *, read_only: bool = False) -> None:
        self.path = str(path)
        self.read_only = read_only
        self._lock = threading.RLock()
        self._depth = 0
        if read_only:
            uri = f"file:{Path(self.path).resolve()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
        else:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if not read_only:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT INTO schema_info(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SCHEMA_VERSION,)
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic unit of work. Nested calls use savepoints.

        The outermost level uses ``BEGIN IMMEDIATE`` so that validation and
        protected execution inside one transaction see a stable snapshot.
        """
        with self._lock:
            depth = self._depth
            savepoint = f"sp_{depth}"
            self._conn.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO {savepoint}")
                    self._conn.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._depth -= 1
                self._conn.execute("COMMIT" if depth == 0 else f"RELEASE {savepoint}")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def next_id(self, prefix: str, width: int = 3) -> str:
        """Deterministic, monotonically increasing IDs such as ``action_001``."""
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO counters(name, value) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET value = value + 1",
                (prefix,),
            )
            (value,) = conn.execute("SELECT value FROM counters WHERE name = ?", (prefix,)).fetchone()
        return f"{prefix}_{value:0{width}d}"
