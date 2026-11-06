"""Shared fixtures. Every test gets an isolated SQLite database under tmp_path."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import ManualClock, SteppingClock
from swarmguard.evaluation.scenarios import APPROVED_EMAIL
from swarmguard.models.decisions import GatewayResponse

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.fixture
def clock() -> ManualClock:
    return SteppingClock()


@pytest.fixture
def sim(tmp_path, clock):
    simulator = SwarmSimulator(tmp_path / "test.db", clock=clock)
    yield simulator
    simulator.close()


@pytest.fixture
def workflow(sim) -> str:
    sim.start_workflow("wf_test", "Test workflow")
    return "wf_test"


def make_email(sim: SwarmSimulator, workflow_id: str, **overrides: str) -> str:
    r = run(sim.operations.prepare_email(workflow_id, **{**APPROVED_EMAIL, **overrides}))
    assert r.executed, r
    return r.execution_result["resource_id"]


def approve(sim: SwarmSimulator, workflow_id: str, email_id: str, version: int = 1, **approve_kwargs: Any) -> str:
    r = run(sim.operations.request_send_approval(workflow_id, email_id, version))
    assert r.executed, r
    approval_id = r.execution_result["approval_id"]
    sim.operator.approve(sim.operator.review(approval_id), **approve_kwargs)
    return approval_id


def modify(sim: SwarmSimulator, workflow_id: str, email_id: str, version: int, **changes: str) -> GatewayResponse:
    return run(sim.operations.modify_email(workflow_id, email_id, version, **changes))


def send(sim: SwarmSimulator, workflow_id: str, email_id: str, version: int, approval_id: str | None,
         **fields: str) -> GatewayResponse:
    return run(sim.operations.propose(workflow_id, "send_email", {**APPROVED_EMAIL, **fields},
                                      resource_id=email_id, resource_version=version, approval_id=approval_id))


def sent_count(sim: SwarmSimulator) -> int:
    return len(sim.sent_emails())
