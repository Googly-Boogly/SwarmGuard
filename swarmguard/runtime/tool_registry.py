"""Read-only metadata over the actual policy tools. Executors remain gateway-private."""
from __future__ import annotations
import hashlib
import json
from types import MappingProxyType
from typing import Any
from swarmguard.security.policy_engine import DEFAULT_POLICY, SecurityPolicy


class TrustedToolRegistry:
    def __init__(self, policy: SecurityPolicy = DEFAULT_POLICY) -> None:
        self._tools = MappingProxyType({name: {
            'tool_name': name, 'tool_version': '1', 'parameter_schema': spec.params_model.model_json_schema(),
            'required_roles': sorted(role.value for role in policy.role_permissions if name in policy.permissions_for(role)),
            'approval_required': spec.requires_approval, 'resource_required': spec.resource_type is not None,
            'resource_version_required': spec.requires_version, 'protected': spec.protected,
            'execution_mode': 'SIMULATED', 'executor_reference': 'gateway-private HMAC-permitted MockToolExecutor',
        } for name, spec in policy.tools.items()})

    def describe(self) -> list[dict[str, Any]]:
        return json.loads(json.dumps(list(self._tools.values())))

    def schema_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.describe(), sort_keys=True).encode()).hexdigest()

    def require(self, tool: str) -> dict[str, Any]:
        if tool not in self._tools:
            raise ValueError('tool is not in the trusted gateway registry')
        return json.loads(json.dumps(self._tools[tool]))
