"""Trusted host / administrator CLI for Stage 7. Never run inside an agent runtime.

  python stage7_cli.py init-secrets --dir deployment/secrets
  python stage7_cli.py migrate --database URL [--grants]
  python stage7_cli.py bootstrap --database URL --approval-key FILE --approvers operator_alice
  python stage7_cli.py issue-token --issuer-key FILE --agent operations_agent --runtime runtime_alpha --out FILE
  python stage7_cli.py approve --database URL --approval-key FILE --approvers operator_alice --approval ID --approver ID
  python stage7_cli.py checkpoint --database URL --dir DIR
"""
from __future__ import annotations
import argparse
import json
import secrets
import sys
from datetime import timedelta
from pathlib import Path
from swarmguard.authentication.keys import generate_keypair, load_private_key, random_secret, write_secret
from swarmguard.authentication.service_identity import CredentialIssuer
from swarmguard.clock import SystemClock
from swarmguard.storage.database import Database

SECRET_FILES = ("identity_issuer.key", "identity_issuer.pub", "executor_grant.key", "executor_grant.pub",
                "approval_signing.key", "db_owner.password", "db_gateway.password", "db_executor.password",
                "db_control.password")


def init_secrets(directory: Path) -> list[str]:
    existing = [n for n in SECRET_FILES if (directory / n).exists()]
    if existing:
        raise SystemExit(f"refusing to overwrite existing secrets: {existing}")
    issuer_private, issuer_public = generate_keypair()
    grant_private, grant_public = generate_keypair()
    write_secret(directory / "identity_issuer.key", issuer_private)      # host only: never mounted in a container
    write_secret(directory / "identity_issuer.pub", issuer_public, 0o644)
    write_secret(directory / "executor_grant.key", grant_private)        # gateway only
    write_secret(directory / "executor_grant.pub", grant_public, 0o644)  # executor
    write_secret(directory / "approval_signing.key", random_secret(32).hex().encode())
    for role in ("owner", "gateway", "executor", "control"):
        write_secret(directory / f"db_{role}.password", secrets.token_urlsafe(32).encode())
    return list(SECRET_FILES)


def control_plane(args: argparse.Namespace):
    from swarmguard.services.control_plane import ControlPlane
    key = Path(args.approval_key).read_bytes().strip()
    return ControlPlane(Database(args.database, migrate=False), SystemClock(), approval_signing_key=key,
                        approver_ids=[a.strip() for a in args.approvers.split(",") if a.strip()])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SwarmGuard Stage 7 trusted administration")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("init-secrets"); s.add_argument("--dir", type=Path, required=True)
    s = sub.add_parser("migrate"); s.add_argument("--database", required=True); s.add_argument("--grants", action="store_true")
    for name in ("bootstrap", "approve"):
        s = sub.add_parser(name)
        s.add_argument("--database", required=True); s.add_argument("--approval-key", required=True)
        s.add_argument("--approvers", default="operator_alice")
        if name == "bootstrap":
            s.add_argument("--workflow", default="wf_remote")
            s.add_argument("--runtime", default="runtime_alpha")
        else:
            s.add_argument("--approval", required=True); s.add_argument("--approver", required=True)
    s = sub.add_parser("issue-token")
    s.add_argument("--issuer-key", required=True); s.add_argument("--agent", required=True)
    s.add_argument("--runtime", required=True); s.add_argument("--ttl-seconds", type=int, default=3600)
    s.add_argument("--out", type=Path)
    s = sub.add_parser("checkpoint"); s.add_argument("--database", required=True); s.add_argument("--dir", type=Path, required=True)
    args = p.parse_args(argv)

    if args.command == "init-secrets":
        print(json.dumps({"created": init_secrets(args.dir), "directory": str(args.dir)}, indent=2))
    elif args.command == "migrate":
        db = Database(args.database, migrate=False)
        applied = db.apply_migrations()
        out = {"applied": applied, "schema_migrations": db.applied_migrations(), "database": db.display_path}
        if args.grants:
            from swarmguard.storage.postgres_roles import apply_role_grants
            out["grants"] = len(apply_role_grants(db))
        print(json.dumps(out))
    elif args.command == "bootstrap":
        cp = control_plane(args)
        cp.seed_synthetic_records()
        out = {}
        if cp.monitor.get_workflow(args.workflow) is None:
            out["workflow"] = cp.create_workflow(args.workflow, "Remote customer communication",
                                                 ["coordinator", "research_agent", "operations_agent"], by="local_admin")["workflow_id"]
        if cp.identities.runtime(args.runtime) is None:
            cp.register_runtime(args.runtime, "Untrusted synthetic agent runtime", by="local_admin")
            cp.bind_agent("operations_agent", args.runtime, "operations", [args.workflow], None, by="local_admin")
            cp.bind_agent("research_agent", args.runtime, "research", [args.workflow], None, by="local_admin")
            out["runtime"] = args.runtime
        print(json.dumps({"bootstrapped": out}))
    elif args.command == "issue-token":
        issuer = CredentialIssuer(load_private_key(args.issuer_key))
        token, jti = issuer.issue(agent_id=args.agent, runtime_id=args.runtime, lifetime=timedelta(seconds=args.ttl_seconds))
        if args.out:
            write_secret(args.out, token.encode(), 0o644 if args.out.parent.name == "agent" else 0o600)
            print(json.dumps({"credential_id": jti, "written": str(args.out)}))
        else:
            print(token)
    elif args.command == "approve":
        cp = control_plane(args)
        packet = cp.approvals.review_packet(args.approval)
        print(json.dumps({"reviewed": packet.model_dump(mode="json")}, sort_keys=True))
        record = cp.grant_approval(args.approval, approver_id=args.approver, operation_hash=packet.operation_hash)
        print(json.dumps({"approved": record["approval_id"], "approver_id": record["approver_id"]}))
    elif args.command == "checkpoint":
        from swarmguard.audit.verification import write_checkpoint
        print(json.dumps({"checkpoint": str(write_checkpoint(Database(args.database, migrate=False), args.dir))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
