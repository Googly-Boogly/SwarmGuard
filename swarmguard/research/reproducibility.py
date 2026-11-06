"""Canonical artifact hashes and environment metadata, without environment secrets."""
from __future__ import annotations

import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def source_hash(pattern: str = "swarmguard/**/*.py") -> str:
    root = Path(__file__).resolve().parents[2]
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.glob(pattern))}
    return digest(files)


def environment() -> dict[str, Any]:
    packages = {}
    for name in ("swarmguard", "pydantic", "fastapi", "openai", "langgraph", "crewai", "autogen-core"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "platform": platform.system(), "packages": packages}
