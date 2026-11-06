"""Environment/secret-file configuration. Secrets are read from files (Docker secrets), never logged."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from swarmguard.models.actions import ID_PATTERN


class ConfigurationError(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise ConfigurationError(f"{name} is required")
    return value


def secret_bytes(name: str) -> bytes:
    path = Path(_env(name))
    try:
        data = path.read_bytes().strip()
    except OSError as exc:
        raise ConfigurationError(f"{name} points to an unreadable secret file") from exc
    if len(data) < 32:
        raise ConfigurationError(f"{name} secret is too short")
    return data


def approver_ids() -> list[str]:
    ids = [i.strip() for i in os.environ.get("SWARMGUARD_APPROVER_IDS", "human_operator").split(",") if i.strip()]
    if not ids or not all(re.fullmatch(ID_PATTERN, i) for i in ids):
        raise ConfigurationError("SWARMGUARD_APPROVER_IDS must be a comma-separated list of identifiers")
    return ids


@dataclass(frozen=True)
class CommonSettings:
    database_url: str
    migrate: bool
    approval_key: bytes = field(repr=False)
    approvers: tuple[str, ...]
    heartbeat_seconds: float

    @staticmethod
    def from_env() -> "CommonSettings":
        return CommonSettings(database_url=_env("SWARMGUARD_DATABASE_URL"),
                              migrate=os.environ.get("SWARMGUARD_MIGRATE", "false").lower() == "true",
                              approval_key=secret_bytes("SWARMGUARD_APPROVAL_KEY_FILE"),
                              approvers=tuple(approver_ids()),
                              heartbeat_seconds=float(os.environ.get("SWARMGUARD_HEARTBEAT_SECONDS", "5")))
