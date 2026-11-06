"""Dependency detection is not a claim of successful execution verification."""
from __future__ import annotations
from importlib.metadata import version, PackageNotFoundError
from typing import Any
import platform

PACKAGES = {'custom': None, 'crewai': 'crewai', 'langgraph': 'langgraph', 'autogen': 'autogen-core'}
SUPPORTED = {'custom': None, 'crewai': (1, 15), 'langgraph': (1,), 'autogen': (0, 7)}


def framework_info(framework: str) -> dict[str, Any]:
    if framework not in PACKAGES:
        raise ValueError('unknown framework')
    package = PACKAGES[framework]
    detected = platform.python_version() if framework == 'custom' else None
    if package:
        try:
            detected = version(package)
        except PackageNotFoundError:
            pass
    supported = framework == 'custom' or (detected is not None and
        tuple(detected.split('.')[:len(SUPPORTED[framework])]) == tuple(str(x) for x in SUPPORTED[framework]))
    return {'framework': framework, 'package': package, 'framework_version': detected,
            'status': 'AVAILABLE' if supported else ('UNSUPPORTED' if detected else 'DISCONNECTED'),
            'installed': framework == 'custom' or detected is not None, 'compatible_version_family': supported,
            'verification': 'not yet executed in this runtime', 'capabilities': {
                'agent_lifecycle_observation': supported, 'tool_call_observation': supported,
                'tool_call_interception': supported, 'trusted_execution_wrapping': supported,
                'human_approval_integration': supported, 'workflow_graph_reconstruction': supported, 'event_replay': supported,
                'automatic_global_hooks': False, 'remote_transport': False},
            'scope': 'Explicit host registration and gateway-owned wrappers only; no control of alternate tool paths.'}


def available_frameworks() -> list[dict[str, Any]]:
    return [framework_info(name) for name in PACKAGES]


def adapter_class(framework: str):
    from swarmguard.integrations.custom.adapter import CustomAdapter
    from swarmguard.integrations.crewai.adapter import CrewAIAdapter
    from swarmguard.integrations.langgraph.adapter import LangGraphAdapter
    from swarmguard.integrations.autogen.adapter import AutoGenAdapter
    return {'custom': CustomAdapter, 'crewai': CrewAIAdapter, 'langgraph': LangGraphAdapter, 'autogen': AutoGenAdapter}[framework]
