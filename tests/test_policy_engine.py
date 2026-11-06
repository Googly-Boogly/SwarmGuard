"""Deterministic policy engine (spec items 11-12 and schema validation)."""

from __future__ import annotations

import pytest

from swarmguard.agents.simulator import RESEARCH_ID
from swarmguard.models.decisions import Decision, ReasonCode
from swarmguard.security.identity import AgentRole
from swarmguard.security.policy_engine import DEFAULT_POLICY, is_reserved_email_domain
from tests.conftest import make_email, run

SPEC_REASON_CODES = {"AUTHORIZED", "UNKNOWN_TOOL", "AGENT_NOT_AUTHORIZED", "INVALID_ACTION", "APPROVAL_REQUIRED",
                     "APPROVAL_NOT_FOUND", "APPROVAL_MISMATCH", "APPROVAL_EXPIRED", "APPROVAL_REJECTED",
                     "APPROVAL_ALREADY_USED", "RESOURCE_VERSION_MISMATCH", "WORKFLOW_MISMATCH",
                     "INVALID_AGENT_IDENTITY"}


def test_stable_reason_codes_present():
    assert SPEC_REASON_CODES <= {c.value for c in ReasonCode}


def test_permission_matrix_matches_specification():
    p = DEFAULT_POLICY.role_permissions
    assert {"read_document"} <= p[AgentRole.COORDINATOR] & p[AgentRole.RESEARCH] & p[AgentRole.OPERATIONS]
    for tool in ("prepare_email", "modify_email", "send_email", "request_approval"):
        assert tool in p[AgentRole.OPERATIONS]
        assert tool not in p[AgentRole.COORDINATOR] and tool not in p[AgentRole.RESEARCH]
    for role in AgentRole:
        assert not {t for t in p[role] if "policy" in t or "approve" in t or "grant" in t}
    assert DEFAULT_POLICY.tools["send_email"].protected and DEFAULT_POLICY.tools["send_email"].requires_approval


def test_11_unknown_tools_rejected(sim, workflow):
    for tool in ("delete_database", "send_email_v2", "shell"):
        r = run(sim.operations.propose(workflow, tool, {}))
        assert (r.decision, r.reason_code, r.executed) == (Decision.BLOCK, ReasonCode.UNKNOWN_TOOL, False)


def test_12_unauthorized_tool_requests_blocked(sim, workflow):
    r = run(sim.coordinator.propose(workflow, "prepare_email",
                                    {"recipient": "a@example.test", "subject": "s", "body": "b"}))
    assert r.reason_code is ReasonCode.AGENT_NOT_AUTHORIZED


@pytest.mark.parametrize("params", [
    {"recipient": "not-an-email", "subject": "s", "body": "b"},       # malformed argument
    {"recipient": "a@example.test", "subject": "s"},                  # missing mandatory field
    {"recipient": "a@example.test", "subject": "s", "body": "b", "bcc": "x@example.test"},  # unsupported param
    {"recipient": "a@example.test", "subject": 5, "body": "b"},       # wrong type (strict, no coercion)
    {"recipient": "a@example.test", "subject": "", "body": "b"},      # empty subject
])
def test_malformed_parameters_rejected(sim, workflow, params):
    r = run(sim.operations.propose(workflow, "prepare_email", params))
    assert (r.decision, r.reason_code) == (Decision.BLOCK, ReasonCode.INVALID_ACTION)
    assert r.details.get("errors")


def test_invalid_resource_references_rejected(sim, workflow):
    r = run(sim.research.read(workflow, "read_document", "doc_does_not_exist"))
    assert r.reason_code is ReasonCode.RESOURCE_NOT_FOUND
    r = run(sim.research.read(workflow, "read_document", "cust_001"))  # wrong resource type
    assert r.reason_code is ReasonCode.INVALID_ACTION
    r = run(sim.research.propose(workflow, "read_document"))  # resource required
    assert r.reason_code is ReasonCode.INVALID_ACTION
    r = run(sim.operations.propose(workflow, "prepare_email", {"recipient": "a@example.test", "subject": "s",
                                                               "body": "b"}, resource_id="doc_service_faq"))
    assert r.reason_code is ReasonCode.INVALID_ACTION


def test_unauthorized_workflows_rejected(sim, workflow):
    r = run(sim.research.read("wf_nope", "read_document", "doc_service_faq"))
    assert r.reason_code is ReasonCode.WORKFLOW_NOT_FOUND
    sim.start_workflow("wf_private", "private", participants=["coordinator"])
    r = run(sim.research.read("wf_private", "read_document", "doc_service_faq"))
    assert r.reason_code is ReasonCode.NOT_WORKFLOW_PARTICIPANT
    sim.start_workflow("wf_other", "other")
    other_email = make_email(sim, "wf_other")
    r = run(sim.operations.read(workflow, "read_email_draft", other_email))
    assert r.reason_code is ReasonCode.WORKFLOW_MISMATCH
    first = run(sim.research.read("wf_other", "read_document", "doc_service_faq"))
    r = run(sim.research.read(workflow, "read_document", "doc_service_faq", parent_action_ids=[first.action_id]))
    assert r.reason_code is ReasonCode.WORKFLOW_MISMATCH


def test_recipient_domains_restricted_to_reserved_examples(sim, workflow):
    assert is_reserved_email_domain("customer@example.test") and is_reserved_email_domain("x@sub.example.com")
    assert not is_reserved_email_domain("x@gmail.com")
    r = run(sim.operations.prepare_email(workflow, recipient="someone@gmail.com", subject="s", body="b"))
    assert r.reason_code is ReasonCode.RECIPIENT_NOT_ALLOWED


def test_task_rules(sim, workflow):
    r = run(sim.coordinator.assign_task(workflow, "outsider", "x"))
    assert r.reason_code is ReasonCode.INVALID_ASSIGNEE
    r = run(sim.coordinator.assign_task(workflow, RESEARCH_ID, "x", depends_on_task_ids=["task_999"]))
    assert r.reason_code is ReasonCode.INVALID_ACTION
    t = run(sim.coordinator.assign_task(workflow, RESEARCH_ID, "x"))
    tid = t.execution_result["resource_id"]
    r = run(sim.operations.propose(workflow, "complete_task", {}, resource_id=tid, resource_version=1))
    assert r.reason_code is ReasonCode.RESOURCE_ACCESS_DENIED
    assert run(sim.research.propose(workflow, "complete_task", {}, resource_id=tid, resource_version=1)).executed


def test_policy_is_deterministic(sim, workflow):
    params = {"recipient": "a@example.test", "subject": "s", "body": "b"}
    results = {run(sim.coordinator.propose(workflow, "prepare_email", params)).reason_code for _ in range(5)}
    assert results == {ReasonCode.AGENT_NOT_AUTHORIZED}
