"""Common host adapter contract. Implementations contain no independent safety logic."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any
from swarmguard.integrations.event_normalizer import FrameworkEvent, CanonicalEvent
from swarmguard.integrations.registry import framework_info
from swarmguard.runtime.middleware import IntegrationRuntime
from swarmguard.sdk.client import SwarmGuardClient, ToolInvocation, ToolResult


class FrameworkAdapter(ABC):
    framework: str

    def __init__(self, runtime: IntegrationRuntime) -> None:
        if runtime.config.framework != self.framework:
            raise ValueError('adapter does not match immutable integration configuration')
        self.runtime = runtime
        self._agents: dict[str, object] = {}
        self._initialized = False

    @abstractmethod
    def check_dependencies(self) -> None: ...

    @abstractmethod
    def make_tool(self, agent: object) -> Any: ...

    @abstractmethod
    async def invoke(self, agent: object, invocation: ToolInvocation) -> ToolResult: ...

    async def initialize(self) -> None:
        if self._initialized:
            raise ValueError('duplicate initialization')
        info = framework_info(self.framework)
        rt = self.runtime
        iid = rt.sim.db.next_id('integration')
        rt.integration_id = iid
        capabilities = dict(info['capabilities'])
        if rt.config.mode == 'OBSERVABILITY':
            for key in ('tool_call_interception', 'trusted_execution_wrapping', 'human_approval_integration'):
                capabilities[key] = False
        record = {**info, 'integration_id': iid, 'status': 'INITIALIZING', 'enabled': True,
            'mode': rt.config.mode, 'monitoring': rt.config.monitoring, 'capabilities': capabilities,
            'verification': 'initialized public APIs; workflow execution not yet verified',
            'workflow_ids': [rt.workflow_id], 'registered_agents': [], 'tools': rt.tools.describe(),
            'errors': [], 'created_at': rt.sim.clock.now().isoformat(), 'execution_boundary': info['scope']}
        rt.repo.create('integration', iid, record, actor='swarmguard.host', occurred_at=rt.sim.clock.now().isoformat())
        try:
            if not info['compatible_version_family']:
                raise RuntimeError(f'{self.framework} unavailable or unsupported: {info["framework_version"]}; install its optional dependency group')
            self.check_dependencies()
        except Exception as exc:
            self.update_record(status='ERROR', errors=[f'{type(exc).__name__}: {str(exc)[:400]}'])
            raise
        self.update_record(status='CONNECTED')
        self._initialized = True

    async def register_agent(self, agent: object, trusted_agent_id: str) -> SwarmGuardClient:
        if not self._initialized:
            raise RuntimeError('initialize adapter first')
        client = self.runtime.bind(agent, trusted_agent_id)
        self._agents[trusted_agent_id] = agent
        self.update_record(registered_agents=list(self._agents))
        return client

    def update_record(self, **changes: Any) -> None:
        rt = self.runtime
        record, version = rt.repo.get('integration', rt.integration_id)
        record.update(changes)
        rt.repo.save('integration', rt.integration_id, record, expected_version=version,
                    actor='swarmguard.host', occurred_at=rt.sim.clock.now().isoformat())

    async def normalize_event(self, agent: object, event: FrameworkEvent) -> CanonicalEvent:
        aid = self.runtime.mapping.resolve(agent)
        return self.runtime.event(aid, event.event_type, source='FRAMEWORK_REPORTED', raw=event,
                                  tool_call_id=event.tool_call_id)

    async def evaluate_tool_request(self, agent: object, invocation: ToolInvocation) -> ToolResult:
        return await self.runtime.client(agent).execute_protected_tool(invocation)

    async def run_pipeline(self, stages: list[Callable[[], Awaitable[None]]]) -> None:
        for stage in stages:
            await stage()

    async def close(self) -> None:
        if self._initialized:
            self.update_record(status='DISCONNECTED')
