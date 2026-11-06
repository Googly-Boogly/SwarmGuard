"""Simulated (untrusted) agents, the trusted simulator, and the shared workspace.

Imports are lazy so that trusted modules can import ``swarmguard.agents.workspace``
without importing the agent classes (which would create an import cycle).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BaseAgent": "swarmguard.agents.base",
    "CoordinatorAgent": "swarmguard.agents.coordinator",
    "ResearchAgent": "swarmguard.agents.research_agent",
    "OperationsAgent": "swarmguard.agents.operations_agent",
    "SwarmSimulator": "swarmguard.agents.simulator",
    "SharedWorkspace": "swarmguard.agents.workspace",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return getattr(import_module(_EXPORTS[name]), name)
    raise AttributeError(name)
