"""SQLite / PostgreSQL persistence.

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

import os
import sqlite3
import threading
from datetime import UTC, datetime
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
SCHEMA_VERSION = "7"

# Stage 7 (Boundaries B/C): service identities, protected-operation state, control state and the
# audit hash chain. Forward-only: never drops or rewrites earlier tables.
STAGE7_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_chain (
    seq INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE,
    chained_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS service_runtimes (
    runtime_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    revocation_reason TEXT
);
CREATE TABLE IF NOT EXISTS agent_bindings (
    agent_id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL REFERENCES service_runtimes(runtime_id),
    role TEXT NOT NULL,
    workflows_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_bindings_runtime ON agent_bindings(runtime_id);
CREATE TABLE IF NOT EXISTS revoked_credentials (
    jti TEXT PRIMARY KEY,
    revoked_at TEXT NOT NULL,
    revoked_by TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS protected_operations (
    operation_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    action_id TEXT UNIQUE,
    authorization_digest TEXT,
    expected_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL CHECK (state IN ('PENDING','AUTHORIZED','EXECUTING','SUCCEEDED','FAILED','BLOCKED','UNKNOWN')),
    reason_code TEXT,
    response_json TEXT,
    result_json TEXT,
    grant_expires_at TEXT,
    correlation_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (agent_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_operations_state ON protected_operations(state);
CREATE INDEX IF NOT EXISTS idx_operations_workflow ON protected_operations(workflow_id);
CREATE TABLE IF NOT EXISTS operation_transitions (
    operation_id TEXT NOT NULL REFERENCES protected_operations(operation_id),
    version INTEGER NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason_code TEXT,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (operation_id, version)
);
CREATE TABLE IF NOT EXISTS control_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS service_heartbeats (
    service TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
"""

# Ordered, forward-only migrations. Every statement is idempotent (IF NOT EXISTS) so that
# databases created by earlier stages upgrade in place without data loss.
MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (1, "stage1_6_base_schema", SCHEMA),
    (2, "stage7_security_infrastructure", STAGE7_SCHEMA),
)
LATEST_MIGRATION = MIGRATIONS[-1][0]

_PG_ADVISORY_LOCK = 7_431_007  # one database-wide writer lock: PostgreSQL equivalent of BEGIN IMMEDIATE

try:  # optional dependency: only required for PostgreSQL URLs
    import psycopg as _psycopg
except ImportError:  # pragma: no cover - exercised only without the deploy extra
    _psycopg = None

INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,) + (
    (_psycopg.IntegrityError,) if _psycopg else ())
OPERATIONAL_ERRORS: tuple[type[BaseException], ...] = (sqlite3.OperationalError,) + (
    (_psycopg.OperationalError, _psycopg.errors.UndefinedTable) if _psycopg else ())
DATABASE_ERRORS: tuple[type[BaseException], ...] = (sqlite3.Error,) + ((_psycopg.Error,) if _psycopg else ())


def is_postgres_url(path: str | Path) -> bool:
    return str(path).startswith(("postgresql://", "postgres://"))


def redact_dsn(path: str | Path) -> str:
    """Display form of a database location; never includes credentials."""
    text = str(path)
    if not is_postgres_url(text):
        return text
    scheme, rest = text.split("://", 1)
    host = rest.rsplit("@", 1)[-1]
    return f"{scheme}://***@{host}"


class _Row(tuple):
    """sqlite3.Row-compatible row: index, column-name and ``keys()`` access."""

    _names: tuple[str, ...] = ()

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            return tuple.__getitem__(self, self._names.index(key))
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._names)


def _pg_row_factory(cursor: Any) -> Any:
    names = tuple(d.name for d in (cursor.description or ()))
    row_type = type("Row", (_Row,), {"_names": names})
    return lambda values: row_type(values)


def _translate(sql: str, has_params: bool) -> str:
    """SQLite dialect -> PostgreSQL: ``?`` placeholders outside literals; ``%`` escaped."""
    out, quoted = [], False
    for ch in sql:
        if ch == "'":
            quoted = not quoted
            out.append(ch)
        elif ch == "%" and has_params:
            out.append("%%")
        elif ch == "?" and not quoted:
            out.append("%s")
        else:
            out.append(ch)
    text = "".join(out)
    text = text.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    text = text.replace("DO UPDATE SET value = value + 1", "DO UPDATE SET value = counters.value + 1")
    return text


def _pg_params(params: Sequence[Any]) -> tuple[Any, ...]:
    # SQLite stores booleans as INTEGER; keep the same representation in PostgreSQL.
    return tuple(int(p) if isinstance(p, bool) else p for p in params)


class _PgConnection:
    """Minimal sqlite3.Connection-compatible facade over one psycopg connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        has = bool(params)
        return self._conn.execute(_translate(sql, has), _pg_params(params) if has else None)

    def executescript(self, script: str) -> None:
        self._conn.execute(_translate(script, False))

    def close(self) -> None:
        self._conn.close()


def _statements(script: str) -> list[str]:
    """Split a schema script into statements (schema scripts contain no ';' inside literals)."""
    lines = [line.split("--", 1)[0] for line in script.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _pg_password() -> str | None:
    path = os.environ.get("SWARMGUARD_DB_PASSWORD_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return None


class Database:
    """Thin, thread-safe wrapper around one SQLite or PostgreSQL connection.

    SQLite (default) serves local development and deterministic tests. PostgreSQL is selected by
    a ``postgresql://`` URL; passwords come from ``SWARMGUARD_DB_PASSWORD_FILE`` (never the URL),
    so ``path`` is safe to pass between trusted components. Outermost transactions take one
    database-wide writer lock in both engines, so check-then-act code keeps its semantics.
    """

    def __init__(self, path: str | Path = ":memory:", *, read_only: bool = False, migrate: bool = True,
                 connect_timeout: int = 5) -> None:
        self.path = str(path)
        self.read_only = read_only
        self.engine = "postgresql" if is_postgres_url(self.path) else "sqlite"
        self._lock = threading.RLock()
        self._depth = 0
        if self.engine == "postgresql":
            if _psycopg is None:
                raise RuntimeError("PostgreSQL URL configured but psycopg is not installed (pip install -e '.[deploy]')")
            conn = _psycopg.connect(self.path, autocommit=True, row_factory=_pg_row_factory,
                                    connect_timeout=connect_timeout, password=_pg_password(),
                                    application_name="swarmguard")
            if read_only:
                conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            self._conn: Any = _PgConnection(conn)
        elif read_only:
            uri = f"file:{Path(self.path).resolve()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
        else:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if self.engine == "sqlite":
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            if not read_only:
                busy = int(os.environ.get("SWARMGUARD_DB_BUSY_TIMEOUT_MS", "5000"))
                self._conn.execute(f"PRAGMA busy_timeout = {max(0, min(busy, 60000))}")
        if migrate and not read_only:
            self.apply_migrations()

    # ------------------------------------------------------------------ migrations

    def apply_migrations(self) -> list[int]:
        """Apply pending forward migrations; returns the versions applied now."""
        applied_now: list[int] = []
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                         "name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            done = {int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for version, name, script in MIGRATIONS:
                # Base schema is re-applied idempotently so earlier-stage databases gain new tables.
                if version in done and version != 1:
                    continue
                for statement in _statements(script):
                    if statement:
                        conn.execute(statement)
                if version not in done:
                    conn.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                                 (version, name, datetime.now(UTC).isoformat()))
                    applied_now.append(version)
            conn.execute("INSERT INTO schema_info(key, value) VALUES ('schema_version', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SCHEMA_VERSION,))
        return applied_now

    def applied_migrations(self) -> list[int]:
        try:
            return sorted(int(r[0]) for r in self.fetchall("SELECT version FROM schema_migrations"))
        except OPERATIONAL_ERRORS:
            return []

    def pending_migrations(self) -> list[int]:
        applied = set(self.applied_migrations())
        return [v for v, _, _ in MIGRATIONS if v not in applied]

    # ------------------------------------------------------------------ connection

    @property
    def display_path(self) -> str:
        return redact_dsn(self.path)

    def ping(self) -> bool:
        try:
            self.fetchone("SELECT 1")
            return True
        except DATABASE_ERRORS:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Atomic unit of work. Nested calls use savepoints.

        The outermost level uses ``BEGIN IMMEDIATE`` (SQLite) or ``BEGIN`` plus a transaction-scoped
        advisory lock (PostgreSQL), so validation and protected execution see a stable snapshot and
        concurrent writers in other processes are serialized.
        """
        with self._lock:
            depth = self._depth
            savepoint = f"sp_{depth}"
            if depth == 0:
                if self.engine == "postgresql":
                    self._conn.execute("BEGIN")
                    if not self.read_only:
                        try:
                            self._conn.execute(f"SELECT pg_advisory_xact_lock({_PG_ADVISORY_LOCK})")
                        except BaseException:
                            self._conn.execute("ROLLBACK")
                            raise
                else:
                    self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute(f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                self._depth -= 1
                self._conn.execute("COMMIT" if depth == 0 else f"RELEASE SAVEPOINT {savepoint}")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
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
