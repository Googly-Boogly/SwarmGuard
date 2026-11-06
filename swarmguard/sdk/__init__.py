"""Local SDK.

Agent-facing: ``SwarmGuardClient`` (a bound capability), ``ToolInvocation``, ``ToolResult``.
Host-only: ``LocalSwarmGuard`` and ``run_local_evaluation``; never pass these to agents.
"""
from typing import Any
from swarmguard.sdk.client import SwarmGuardClient, ToolInvocation, ToolResult
from swarmguard.sdk.exceptions import FrameworkUnavailableError, SwarmGuardSDKError

__all__ = ['SwarmGuardClient', 'ToolInvocation', 'ToolResult', 'LocalSwarmGuard', 'run_local_evaluation',
           'FrameworkUnavailableError', 'SwarmGuardSDKError']


def __getattr__(name: str) -> Any:  # lazy: the host facade imports the runtime, which imports this package
    if name in ('LocalSwarmGuard', 'run_local_evaluation'):
        from swarmguard.sdk import host
        return getattr(host, name)
    raise AttributeError(name)
