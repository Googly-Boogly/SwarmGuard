"""Trusted identity registry and runtime context."""

from __future__ import annotations

import pickle

import pytest

from swarmguard.agents.simulator import COORDINATOR_ID, OPERATIONS_ID, RESEARCH_ID
from swarmguard.security.identity import (
    AgentCredential,
    AgentIdentity,
    AgentRole,
    IdentityRegistry,
    RegistrySealedError,
)


def test_registry_issues_opaque_credentials_and_resolves_only_real_ones():
    reg = IdentityRegistry()
    cred = reg.register(AgentIdentity("a1", AgentRole.RESEARCH, "A1"))
    assert reg.resolve(cred).agent_id == "a1"
    assert "redacted" in repr(cred) and cred.token not in repr(cred)
    for bogus in (None, "a1", {"agent_id": "a1"}, AgentCredential("guess"), AgentCredential(cred.token + "x")):
        assert reg.resolve(bogus) is None
    with pytest.raises(TypeError):
        pickle.dumps(cred)


def test_registry_rejects_duplicates_invalid_ids_and_is_sealable():
    reg = IdentityRegistry()
    reg.register(AgentIdentity("a1", AgentRole.RESEARCH, "A1"))
    with pytest.raises(ValueError):
        reg.register(AgentIdentity("a1", AgentRole.OPERATIONS, "dup"))
    with pytest.raises(ValueError):
        reg.register(AgentIdentity("bad id!", AgentRole.OPERATIONS, "x"))
    reg.seal()
    with pytest.raises(RegistrySealedError):
        reg.register(AgentIdentity("a2", AgentRole.OPERATIONS, "A2"))


def test_simulator_registry_is_sealed(sim):
    assert sim.identities.sealed
    assert sim.identities.agent_ids() == sorted([COORDINATOR_ID, RESEARCH_ID, OPERATIONS_ID])


def test_trusted_context_comes_from_policy_and_workflow_membership(sim):
    sim.start_workflow("wf_a", "A")
    sim.start_workflow("wf_b", "B", participants=[COORDINATOR_ID])
    ctx = sim.gateway.trusted_context(sim.identities.get(RESEARCH_ID))
    assert ctx.permissions == sim.policy_engine.permissions_for(AgentRole.RESEARCH)
    assert ctx.authorized_workflows == {"wf_a"}
    assert sim.gateway.trusted_context(sim.identities.get(COORDINATOR_ID)).authorized_workflows == {"wf_a", "wf_b"}
