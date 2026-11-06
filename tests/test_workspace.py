"""Shared workspace reads, versioned mutations, and attribution (spec items 6-10)."""

from __future__ import annotations

import pytest

from swarmguard.agents.simulator import OPERATIONS_ID, RESEARCH_ID, brief_document_id
from swarmguard.agents.workspace import ResourceAccessError, VersionConflictError
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.models.events import EventType
from tests.conftest import make_email, modify, run


def test_6_authorized_reads_succeed(sim, workflow):
    r = run(sim.research.read(workflow, "read_customer_record", "cust_001"))
    assert r.executed and r.execution_result["content"]["email"] == "customer@example.test"
    assert r.execution_result["version"] == 1
    r = run(sim.operations.read(workflow, "read_document", "doc_service_faq"))
    assert r.executed
    # Coordinator's customer-record access is "limited": the record ACL excludes it.
    r = run(sim.coordinator.read(workflow, "read_customer_record", "cust_001"))
    assert r.reason_code is ReasonCode.RESOURCE_ACCESS_DENIED
    reads = sim.monitor.find_events(resource_id="cust_001", event_type=EventType.RESOURCE_READ)
    assert [e.agent_id for e in reads] == [RESEARCH_ID]


def test_7_authorized_mutations_increment_version(sim, workflow):
    email_id = make_email(sim, workflow)
    r = modify(sim, workflow, email_id, 1, body="Second body.")
    assert r.executed and r.execution_result["version"] == 2
    r = modify(sim, workflow, email_id, 2, subject="Third subject")
    assert r.execution_result["version"] == 3
    resource = sim.workspace.get(email_id)
    assert resource.version == 3 and resource.content["subject"] == "Third subject"
    versions = sim.workspace.versions(email_id)
    assert [v.version for v in versions] == [1, 2, 3]
    assert versions[0].content["body"] == "Your service request has been received."  # v1 preserved
    assert versions[1].changes == {"body": {"old": "Your service request has been received.", "new": "Second body."}}


def test_8_unauthorized_modifications_rejected(sim, workflow):
    brief = brief_document_id(workflow)
    r = run(sim.operations.propose(workflow, "update_shared_document", {"content": "x"}, resource_id=brief,
                                   resource_version=1))
    assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.RESOURCE_ACCESS_DENIED)
    r = run(sim.research.propose(workflow, "update_shared_document", {"content": "x"},
                                 resource_id="doc_service_faq", resource_version=1))
    assert r.reason_code is ReasonCode.RESOURCE_ACCESS_DENIED
    assert sim.workspace.get(brief).version == 1
    # Defense in depth: the workspace itself refuses unauthorized writers.
    with pytest.raises(ResourceAccessError):
        sim.workspace.update(brief, {"content": "x"}, agent_id=OPERATIONS_ID, expected_version=1,
                             event_workflow_id=workflow)


def test_9_stale_resource_versions_rejected(sim, workflow):
    email_id = make_email(sim, workflow)
    assert modify(sim, workflow, email_id, 1, body="v2 body").executed
    stale = modify(sim, workflow, email_id, 1, body="overwrite attempt")
    assert (stale.decision, stale.reason_code) == (Decision.BLOCK, ReasonCode.RESOURCE_VERSION_MISMATCH)
    assert stale.details == {"expected_version": 1, "current_version": 2}
    assert sim.workspace.get(email_id).content["body"] == "v2 body"
    missing = run(sim.operations.propose(workflow, "modify_email", {"body": "blind"}, resource_id=email_id))
    assert missing.reason_code is ReasonCode.INVALID_ACTION  # no blind overwrites
    with pytest.raises(VersionConflictError):
        sim.workspace.update(email_id, {"body": "x"}, agent_id=OPERATIONS_ID, expected_version=1,
                             event_workflow_id=workflow)


def test_10_resource_history_identifies_responsible_agent(sim, workflow):
    email_id = make_email(sim, workflow)
    r = modify(sim, workflow, email_id, 1, recipient="other@example.test")
    mods = sim.monitor.find_events(resource_id=email_id, event_type=EventType.RESOURCE_MODIFIED)
    assert len(mods) == 1
    m = mods[0]
    assert (m.agent_id, m.action_id) == (OPERATIONS_ID, r.action_id)
    assert (m.resource_version_before, m.resource_version_after) == (1, 2)
    assert m.details["changes"] == {"recipient": {"old": "customer@example.test", "new": "other@example.test"}}
    v2 = sim.workspace.get_version(email_id, 2)
    assert (v2.agent_id, v2.action_id, v2.event_id) == (OPERATIONS_ID, r.action_id, m.event_id)
    assert sim.monitor.get_action(r.action_id).agent_id == OPERATIONS_ID


def test_noop_modification_does_not_bump_version(sim, workflow):
    email_id = make_email(sim, workflow)
    r = modify(sim, workflow, email_id, 1, body="Your service request has been received.")
    assert r.executed and r.execution_result["status"] == "UNCHANGED" and sim.workspace.get(email_id).version == 1
