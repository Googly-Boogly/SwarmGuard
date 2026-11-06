"""PostgreSQL least-privilege grants for the hardened deployment (applied by the migration job).

Roles are created (with passwords from Docker secrets) by ``deployment/postgres/init-roles.sh``. No
service role may DELETE anything or run DDL. Audit tables are append-only for every service role.
Only ``sg_executor`` may write the simulated outbox. The agent runtime has no database role at all.
"""
from __future__ import annotations
from swarmguard.storage.database import Database

APPEND_ONLY = ("events", "audit_chain", "actions", "resource_versions", "operation_transitions", "sent_emails",
               "revoked_credentials")
ALL_TABLES = ("schema_info", "schema_migrations", "counters", "workflows", "approvals", "resources", "resource_versions",
              "actions", "decisions", "events", "sent_emails", "ethics_evaluations", "agent_messages", "agent_decisions",
              "interventions", "autonomous_assessments", "agent_memory", "agent_registry", "console_jobs",
              "console_job_versions", "research_runs", "research_run_versions", "research_candidates", "research_audit",
              "platform_records", "platform_versions", "integration_events", "audit_chain", "service_runtimes",
              "agent_bindings", "revoked_credentials", "protected_operations", "operation_transitions", "control_state",
              "service_heartbeats")

# role -> (tables with INSERT, tables with UPDATE). Every service role gets SELECT on all tables.
ROLE_WRITES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "sg_gateway": (("counters", "actions", "decisions", "events", "audit_chain", "protected_operations",
                    "operation_transitions", "service_heartbeats"),
                   ("counters", "decisions", "protected_operations", "service_heartbeats", "approvals")),
    "sg_executor": (("counters", "resources", "resource_versions", "events", "audit_chain", "sent_emails", "approvals",
                     "agent_messages", "operation_transitions", "service_heartbeats"),
                    ("counters", "resources", "approvals", "protected_operations", "service_heartbeats")),
    "sg_control": (("counters", "workflows", "resources", "resource_versions", "events", "audit_chain", "approvals",
                    "service_runtimes", "agent_bindings", "revoked_credentials", "control_state", "console_jobs",
                    "console_job_versions", "research_runs", "research_run_versions", "research_candidates",
                    "research_audit", "platform_records", "platform_versions"),
                   ("counters", "workflows", "approvals", "service_runtimes", "agent_bindings", "control_state",
                    "console_jobs", "research_runs", "platform_records")),
}


def grant_statements(schema: str = "public") -> list[str]:
    statements = []
    for role, (inserts, updates) in ROLE_WRITES.items():
        if set(updates) & set(APPEND_ONLY):   # explicit check (asserts are stripped under python -O)
            raise ValueError(f"{role} may not update append-only tables")
        statements.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}")
        statements.append(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        statements.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}")
        statements += [f"GRANT INSERT ON {t} TO {role}" for t in inserts]
        statements += [f"GRANT UPDATE ON {t} TO {role}" for t in updates]
        statements.append(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO {role}")
    return statements


def apply_role_grants(db: Database) -> list[str]:
    if db.engine != "postgresql":
        raise RuntimeError("role grants apply to PostgreSQL deployments only")
    statements = grant_statements()
    with db.transaction():
        for statement in statements:
            db.execute(statement)
    return statements
