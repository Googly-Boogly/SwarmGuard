"""Trusted runtime owner. Agents receive only slotted, credential-bound SDK clients."""
from __future__ import annotations
import asyncio
import hashlib
import time
import json
from collections.abc import Awaitable, Callable
from typing import Any
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.integrations.configuration import IntegrationConfig
from swarmguard.integrations.event_normalizer import FrameworkEvent, CanonicalEvent
from swarmguard.integrations.identity_mapping import IdentityMapping
from swarmguard.models.events import EventType
from swarmguard.models.risk import ObservationWindow
from swarmguard.monitoring.local_monitor import LocalActionMonitor
from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
from swarmguard.runtime.tool_registry import TrustedToolRegistry
from swarmguard.sdk.client import SwarmGuardClient, ToolResult
from swarmguard.storage.repositories import PlatformRepository


class IntegrationRuntime:
    """Host-controlled registration, existing gateway, and read-only assessments.

    No alternate executor or network tool registration exists here. A trusted host
    may attach the simulator's human operator to its existing approval UI.
    """
    def __init__(self, sim: SwarmSimulator, workflow_id: str, config: IntegrationConfig) -> None:
        wf = sim.monitor.get_workflow(workflow_id)
        if wf is None:
            raise ValueError('host must create the workflow before attaching a framework')
        if config.mode == 'ENFORCED' and (sim.gateway._max_workflow_proposals is None or
                sim.gateway._max_workflow_proposals > config.max_proposals):
            raise ValueError('host must configure a gateway proposal limit no greater than the integration limit')
        required_guard = config.monitoring in ('ESCALATE', 'ENFORCE')
        if required_guard and sim.workflow_enforcement is not ObservationWindow.GLOBAL:
            raise ValueError('escalation/enforcement requires the existing global WorkflowGuard at construction')
        if config.ethics_council and sim.gateway._council is None:
            raise ValueError('host must inject a bounded council before requesting council enforcement')
        self.sim, self.workflow_id, self.config = sim, workflow_id, config
        self.mapping = IdentityMapping(set(wf.participants))
        self.tools = TrustedToolRegistry(sim.policy_engine.policy)
        self.repo = PlatformRepository(sim.db)
        self.integration_id: str | None = None
        self._clients: dict[str, SwarmGuardClient] = {}
        self._event_count = 0
        self._deadline = time.monotonic() + config.max_runtime_seconds

    def event(self, aid: str, event_type: str, *, source: str, action_id: str | None = None,
              tool_call_id: str | None = None, tool: str | None = None, parents: list[str] | None = None,
              raw: FrameworkEvent | None = None, metadata: dict[str, Any] | None = None) -> CanonicalEvent:
        if self.integration_id is None:
            raise RuntimeError('integration is not initialized')
        if self._event_count >= self.config.max_events:
            raise RuntimeError('canonical event budget exhausted')
        canonical = CanonicalEvent(event_id=self.sim.db.next_id('canonical'), integration_id=self.integration_id,
            framework=self.config.framework, workflow_id=self.workflow_id, agent_id=aid, event_type=event_type,
            timestamp=self.sim.clock.now(), reported_at=raw.reported_at if raw else None, source=source,
            action_id=action_id, tool_name=tool, tool_call_id=tool_call_id,
            parent_action_ids=parents or [], external_event_id=raw.external_event_id if raw else None,
            metadata=metadata if metadata is not None else (raw.metadata if raw else {}))
        self.repo.append_integration_event(canonical.model_dump(mode='json'))
        self._event_count += 1
        if source == 'FRAMEWORK_REPORTED':
            # Reported execution never becomes an authoritative ACTION_EXECUTED.
            self.sim.monitor.record_event(EventType.FRAMEWORK_OBSERVATION, self.workflow_id, agent_id=aid,
                parent_event_ids=[], summary=f'Untrusted framework report: {event_type}',
                details={'canonical_event_id': canonical.event_id, 'source': source, 'report': canonical.model_dump(mode='json')})
        return canonical

    def bind(self, agent: object, trusted_agent_id: str) -> SwarmGuardClient:
        self.mapping.register(agent, trusted_agent_id)
        gateway_client = self.sim.gateway_client(trusted_agent_id)

        async def submit(data: dict[str, Any]) -> ToolResult:
            if self.config.mode != 'ENFORCED':
                raise PermissionError('observability-only integrations cannot execute tools')
            # Refuse before another proposal if its mandatory canonical audit cannot fit.
            if self._event_count + 4 > self.config.max_events or time.monotonic() >= self._deadline:
                self.sim.monitor.record_event(EventType.SECURITY_ALERT, self.workflow_id,
                    agent_id=trusted_agent_id, summary='Integration runtime/audit limit exhausted; no proposal submitted',
                    details={'integration_id': self.integration_id, 'executed': False})
                raise RuntimeError('canonical audit capacity exhausted; no new operation submitted')
            payload = dict(data)
            call_id = payload.pop('tool_call_id', None)
            claimed_workflow = payload.pop('workflow_id', self.workflow_id)
            if claimed_workflow != self.workflow_id:
                payload['invalid_workflow_binding'] = True  # audited INVALID_ACTION at the gateway
            if call_id is not None:
                if not isinstance(call_id, str) or not 1 <= len(call_id) <= 128:
                    payload['invalid_tool_call_id'] = True
                    call_id = None
                else:
                    payload['request_id'] = 'fw_' + hashlib.sha256(
                        f'{self.integration_id}|{self.workflow_id}|{call_id}'.encode()).hexdigest()[:48]
            payload['workflow_id'] = self.workflow_id
            self.event(trusted_agent_id, 'TOOL_CALL_PROPOSED', source='FRAMEWORK_REPORTED',
                       tool_call_id=call_id if isinstance(call_id, str) else None,
                       tool=str(payload.get('tool', 'invalid')))
            # Never cancel a gateway submission between proposal and persisted decision.
            task = asyncio.create_task(gateway_client.submit(payload))
            try:
                response = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            action = self.sim.monitor.get_action(response.action_id)
            parents = list(action.parent_action_ids) if action else []
            self.event(trusted_agent_id, 'AUTHORIZATION_DECISION', source='GATEWAY_VERIFIED',
                action_id=response.action_id, tool_call_id=call_id, tool=response.tool, parents=parents,
                metadata={'decision': response.decision.value, 'reason_code': response.reason_code.value, 'executed': response.executed})
            self.event(trusted_agent_id, 'TOOL_EXECUTED' if response.executed else 'TOOL_FAILED',
                source='GATEWAY_VERIFIED', action_id=response.action_id, tool_call_id=call_id, tool=response.tool,
                parents=parents, metadata={'executed': response.executed})
            if response.tool == 'request_approval' and response.executed:
                self.event(trusted_agent_id, 'APPROVAL_REQUESTED', source='GATEWAY_VERIFIED',
                           action_id=response.action_id, tool_call_id=call_id, metadata=response.execution_result)
            elif response.executed and response.tool in ('assign_task','send_message'):
                self.event(trusted_agent_id, 'TASK_ASSIGNED' if response.tool == 'assign_task' else 'MESSAGE',
                    source='GATEWAY_VERIFIED', action_id=response.action_id, tool_call_id=call_id,
                    metadata=response.execution_result)
            advice = await self.assess(response.action_id) if action and self.config.collect_monitoring else []
            return ToolResult(gateway=response, advisories=advice, tool_call_id=call_id)

        client = SwarmGuardClient(submit)
        self._clients[trusted_agent_id] = client
        self.event(trusted_agent_id, 'AGENT_CREATED', source='HOST_REGISTERED')
        return client

    def client(self, agent: object) -> SwarmGuardClient:
        return self._clients[self.mapping.resolve(agent)]

    async def assess(self, action_id: str) -> list[dict[str, Any]]:
        builder = self.sim.observation_builder()
        snapshot = builder.snapshot_for_action(action_id)
        out = []
        for monitor, ctx in ((LocalActionMonitor(), builder.local(snapshot, action_id)),
                             (GlobalWorkflowMonitor(), builder.global_(snapshot, action_id))):
            assessment = await monitor.evaluate(ctx.action, ctx)
            raw = assessment.model_dump(mode='json')
            self.sim.repos.autonomous_assessments.insert(self.sim.db.next_id('assessment'), self.workflow_id,
                action_id, monitor.monitor_id, json.dumps(raw))
            out.append(raw)
        return out

    def audit_history(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode='json') for e in self.sim.monitor.get_workflow_history(self.workflow_id)]
