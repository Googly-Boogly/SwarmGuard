"""Provision local operators. Password input is hidden; only salted scrypt hashes are saved.

  python setup_console_operator.py                                   # legacy single operator (.console/operator.json)
  python setup_console_operator.py --username admin_ada --role ADMINISTRATOR [--file .console/operators.json]

Role-based accounts (Stage 7) are appended to the accounts file; an existing username is never overwritten.
Roles are separate: RESEARCHER, OPERATOR, ADMINISTRATOR, AUDITOR. No role implies another.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
from swarmguard.console.auth import ROLES, OperatorAccount, password_hash


def _password() -> str:
    password = getpass.getpass("New operator password (at least 12 characters): ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords differ")
    return password


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(record, out, indent=2)
    path.chmod(0o600)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--username")
    p.add_argument("--role", action="append", choices=ROLES, help="repeatable; required with --username")
    p.add_argument("--file", type=Path, default=Path(".console/operators.json"))
    args = p.parse_args()
    if args.username is None:
        # Provisioning is local filesystem authority; no browser or agent can call it.
        legacy = Path(os.environ.get("SWARMGUARD_OPERATOR_FILE", ".console/operator.json"))
        _write(legacy, {"password_hash": password_hash(_password())})
        print("Local operator configured. Restart the console backend to invalidate old sessions.")
        return
    if not args.role:
        raise SystemExit("--role is required with --username (roles are never implied)")
    record = json.loads(args.file.read_text()) if args.file.exists() else {"accounts": []}
    if any(a["username"] == args.username for a in record["accounts"]):
        raise SystemExit(f"account {args.username} already exists; remove it from {args.file} deliberately first")
    account = OperatorAccount(args.username, password_hash(_password()), frozenset(args.role))
    record["accounts"].append({"username": account.username, "roles": sorted(account.roles),
                               "password_hash": account.password_hash})
    _write(args.file, record)
    print(f"Account {account.username} provisioned with roles {sorted(account.roles)}. Restart the backend.")


if __name__ == "__main__":
    main()
