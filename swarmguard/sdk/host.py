"""Trusted host-side local SDK. Never hand a ``LocalSwarmGuard`` to an agent or model.

The host process owns this object: it creates the workflow, registers framework
objects with trusted identities, hands each agent only its framework tool (or a
bound ``SwarmGuardClient``), and plays the simulated human operator. Agents only
ever see the gateway-bound tool. Importing this module secures nothing by itself:
protection exists only for capabilities actually routed through these tools.

This is an in-process SDK over the synthetic simulator (mock tools, no real email).
There is no remote transport; a remote deployment would need authenticated agent
credentials and a separate service boundary (see docs/RUNTIME_SECURITY.md).
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import Clock, SteppingClock
from swarmguard.integrations.configuration import Framework, IntegrationConfig
from swarmguard.integrations.event_normalizer import CanonicalEvent, FrameworkEvent
from swarmguard.integrations.registry import adapter_class, framework_info
from swarmguard.models.risk import ObservationWindow
from swarmguard.runtime.middleware import IntegrationRuntime
from swarmguard.sdk.client import SwarmGuardClient
from swarmguard.sdk.exceptions import FrameworkUnavailableError


class LocalSwarmGuard:
    """Host-owned local integration: simulator, gateway, one workflow and one adapter."""

    def __init__(self, sim: SwarmSimulator, adapter: Any) -> None:
        self._sim = sim
        self.adapter = adapter
        self.workflow_id: str = adapter.runtime.workflow_id

    @classmethod
    async def open(cls, *, framework: Framework = 'custom', workflow_id: str = 'wf_application',
                   title: str = 'Customer communication', purpose: str | None = 'service_updates',
                   mode: str = 'ENFORCED', monitoring: str = 'OBSERVE', db_path: str | Path = ':memory:',
                   customers: dict[str, dict[str, Any]] | None = None, clock: Clock | None = None) -> 'LocalSwarmGuard':
        info = framework_info(framework)
        if not info['compatible_version_family']:
            raise FrameworkUnavailableError(f'{framework} unavailable or unsupported '
                f'(detected: {info["framework_version"]}); install the optional "{framework}" dependency group')
        config = IntegrationConfig(framework=framework, mode=mode, monitoring=monitoring)
        sim = SwarmSimulator(db_path, clock=clock or SteppingClock(), max_workflow_proposals=config.max_proposals,
                             workflow_enforcement=ObservationWindow.GLOBAL if monitoring in ('ESCALATE', 'ENFORCE') else None)
        try:
            for cid, content in (customers or {}).items():
                sim.seed_customer(cid, content)
            sim.start_workflow(workflow_id, title, purpose=purpose)
            adapter = adapter_class(framework)(IntegrationRuntime(sim, workflow_id, config))
            await adapter.initialize()
        except Exception:
            sim.close()
            raise
        return cls(sim, adapter)

    async def register_agent(self, agent: object, trusted_agent_id: str) -> SwarmGuardClient:
        """Bind a host-owned framework object to a sealed simulator identity."""
        return await self.adapter.register_agent(agent, trusted_agent_id)

    def tool_for(self, agent: object) -> Any:
        """The framework-native, gateway-bound tool for a registered agent."""
        return self.adapter.make_tool(agent)

    async def record_event(self, agent: object, event: FrameworkEvent) -> CanonicalEvent:
        """Record an untrusted framework observation (never execution proof)."""
        return await self.adapter.normalize_event(agent, event)

    # --- trusted human oversight (simulated operator; synthetic) ---------------------------
    def pending_approvals(self) -> list[dict[str, Any]]:
        return [r.model_dump(mode='json') for r in self._sim.approvals.pending() if r.workflow_id == self.workflow_id]

    def review_and_approve(self, approval_id: str) -> dict[str, Any]:
        """The simulated human approves exactly the reviewed operation snapshot."""
        packet = self._sim.operator.review(approval_id)
        return self._sim.operator.approve(packet).model_dump(mode='json')

    def reject(self, approval_id: str, reason: str) -> dict[str, Any]:
        return self._sim.operator.reject(approval_id, reason).model_dump(mode='json')

    # --- read-only audit -------------------------------------------------------------------
    def decisions(self) -> list[dict[str, Any]]:
        return [d.model_dump(mode='json') for d in self._sim.monitor.get_decisions(self.workflow_id)]

    def audit_history(self) -> list[dict[str, Any]]:
        return self.adapter.runtime.audit_history()

    def canonical_events(self) -> list[dict[str, Any]]:
        rt = self.adapter.runtime
        return rt.repo.integration_events(rt.integration_id)

    def workflow_graph(self) -> dict[str, Any] | None:
        graph = self._sim.graph.build_workflow_graph(self.workflow_id)
        return graph.model_dump(mode='json') if graph else None

    async def assess(self, action_id: str) -> list[dict[str, Any]]:
        """Local and global monitor assessments for one action (advisory only)."""
        return await self.adapter.runtime.assess(action_id)

    def simulated_sent_emails(self) -> list[dict[str, Any]]:
        """Simulated outbox. No real email is ever sent."""
        return self._sim.sent_emails(self.workflow_id)

    async def close(self) -> None:
        try:
            await self.adapter.close()
        finally:
            self._sim.close()

    async def __aenter__(self) -> 'LocalSwarmGuard':
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


async def run_local_evaluation(framework: Framework = 'custom', *, db_path: str | Path = ':memory:',
                               application_version: str = 'local-dev') -> dict[str, Any]:
    """Run the deterministic ``swarmguard_core`` suite locally (mock, synthetic)."""
    from swarmguard.evaluation.framework_comparison import ApplicationConfig, run_framework_experiment
    from swarmguard.storage.database import Database
    db = Database(db_path)
    try:
        return await run_framework_experiment(ApplicationConfig(framework=framework,
                                                                application_version=application_version), db)
    finally:
        db.close()
