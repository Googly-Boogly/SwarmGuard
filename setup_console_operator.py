"""Provision one local operator. Password input is hidden and only its scrypt hash is saved."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from swarmguard.console.auth import password_hash


def main() -> None:
    password = getpass.getpass("New local operator password (at least 12 characters): ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords differ")
    root = Path(".console")
    root.mkdir(mode=0o700, exist_ok=True)
    path = root / "operator.json"
    # Provisioning is local filesystem authority; no browser or agent can call it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"password_hash": password_hash(password)}, out)
    path.chmod(0o600)
    print("Local operator configured. Restart the console backend to invalidate old sessions.")


if __name__ == "__main__":
    main()
