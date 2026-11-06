"""Autonomous-agent decisions, observations and results.

``AgentDecision`` is the structured output requested from the agent's model. It is UNTRUSTED:
the runtime validates it, maps it to an ``ActionProposal`` and submits it with the agent's
credential-bound gateway client. ``claimed_agent_id`` is informational only; a mismatch is
blocked by the gateway as identity spoofing. ``summary`` is a model-written statement, not proof
of the model's internal reasoning. Hidden chain-of-thought is never requested or stored.

The schema is strict-JSON-schema compatible: every field is required, and optional values are
nullable. Tool parameters travel as a JSON-object string because strict schemas cannot express
free-form objects.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from swarmguard.models.messages import MessageType


class DecisionType(StrEnum):
    TOOL_REQUEST = "TOOL_REQUEST"
    SEND_MESSAGE = "SEND_MESSAGE"
    DELEGATE_TASK = "DELEGATE_TASK"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    COMPLETE_TASK = "COMPLETE_TASK"
    WAIT = "WAIT"


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_type: DecisionType
    tool: str | None
    resource_id: str | None
    resource_version: int | None
    parameters_json: str | None = Field(description="JSON object of tool parameters, or null")
    approval_id: str | None
    parent_action_ids: list[str]
    recipient_id: str | None = Field(description="Agent ID for messages and delegations")
    message_type: MessageType | None
    message_text: str | None
    task_id: str | None
    task_description: str | None
    depends_on_task_ids: list[str]
    required_tools: list[str]
    summary: str = Field(description="One or two sentences on why (untrusted, not chain-of-thought)")
    claimed_agent_id: str | None = Field(description="Informational only; never used for identity")

    @field_validator("parameters_json")
    @classmethod
    def _json_object(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if not isinstance(json.loads(v), dict):
            raise ValueError("parameters_json must encode a JSON object")
        return v

    def parameters(self) -> dict[str, Any]:
        return json.loads(self.parameters_json) if self.parameters_json else {}


def decision(decision_type: DecisionType | str, **kw: Any) -> dict[str, Any]:
    """Helper: a complete decision dict (unset fields null or empty). Used by mock policies and tests."""
    params = kw.pop("parameters", None)
    base: dict[str, Any] = {f: None for f in AgentDecision.model_fields}
    base.update({"parent_action_ids": [], "depends_on_task_ids": [], "required_tools": [], "summary": ""})
    base["decision_type"] = DecisionType(decision_type).value
    if params is not None:
        base["parameters_json"] = json.dumps(params, sort_keys=True)
    base.update(kw)
    return base


class ActionResult(BaseModel):
    """What the runtime reports back to an agent after routing its decision."""

    model_config = ConfigDict(frozen=True)

    step: int
    decision_type: DecisionType
    accepted: bool = Field(description="False if the decision was invalid and never submitted")
    action_id: str | None = None
    tool: str | None = None
    decision: str | None = Field(default=None, description="Gateway decision (ALLOW/BLOCK/ESCALATE)")
    reason_code: str | None = None
    executed: bool = False
    explanation: str = ""
    result: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = Field(default=None, description="The agent's own submitted proposal (echo)")


class AgentObservation(BaseModel):
    """The permitted view of one agent at one step (private memory, own tasks, inbox, readable resources).
    Never contains trusted security state (policies, approval records, other agents' memory)."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    role: str
    workflow_id: str
    step: int
    objective: str | None = None
    objective_fields: dict[str, Any] = Field(default_factory=dict)
    private_objective: str | None = None
    private_objective_fields: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str]
    workflow_participants: list[str]
    tasks: list[dict[str, Any]]
    inbox: list[dict[str, Any]]
    readable_resources: list[dict[str, Any]]
    my_approval_requests: list[dict[str, Any]]
    memory: list[dict[str, Any]]
    pinned_references: list[str]
    steps_remaining: int
