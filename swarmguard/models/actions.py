"""Action proposal / request models and strict per-tool parameter schemas.

* ``ActionProposal`` is what an *untrusted* agent submits. Any ``agent_id`` it
  carries is only a claim, checked against the authenticated identity.
* ``ActionRequest`` is built by the capability gateway. Its ``action_id``,
  ``created_at`` and ``agent_id`` come from trusted code.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_PARAMETERS_BYTES = 16_384
MAX_PARENTS = 32
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


def _check_parameters_size(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("parameters must be JSON-serializable") from exc
    if len(encoded) > MAX_PARAMETERS_BYTES:
        raise ValueError(f"parameters exceed {MAX_PARAMETERS_BYTES} bytes")
    return value


class ActionProposal(BaseModel):
    """An untrusted proposal. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workflow_id: str = Field(pattern=ID_PATTERN)
    tool: str = Field(pattern=TOOL_NAME_PATTERN)
    resource_id: str | None = Field(default=None, pattern=ID_PATTERN)
    resource_version: int | None = Field(default=None, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    parent_action_ids: list[str] = Field(default_factory=list, max_length=MAX_PARENTS)
    approval_id: str | None = Field(default=None, pattern=ID_PATTERN)
    request_id: str | None = Field(
        default=None, pattern=ID_PATTERN, description="Optional client nonce; reuse is rejected as a replay."
    )
    agent_id: str | None = Field(
        default=None,
        pattern=ID_PATTERN,
        description="UNTRUSTED claim. Never used for authorization; a mismatch is blocked.",
    )

    @field_validator("parameters")
    @classmethod
    def _size_limit(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_parameters_size(value)

    @field_validator("parent_action_ids")
    @classmethod
    def _parents(cls, value: list[str]) -> list[str]:
        for item in value:
            if not re.fullmatch(ID_PATTERN, item):
                raise ValueError("invalid parent action id")
        if len(set(value)) != len(value):
            raise ValueError("duplicate parent action ids")
        return value


class ActionRequest(BaseModel):
    """Gateway-constructed, auditable action. ``agent_id`` is authenticated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    workflow_id: str
    agent_id: str = Field(description="Authenticated by the gateway from the trusted runtime context.")
    tool: str
    resource_id: str | None = None
    resource_version: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    parent_action_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    request_id: str | None = None
    claimed_agent_id: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Strict tool parameter schemas (extra fields forbidden, no type coercion)
# ---------------------------------------------------------------------------


def validate_email_address(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.fullmatch(value):
        raise ValueError("invalid email address")
    return value


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class NoParams(ToolParams):
    """Tools whose only input is the top-level ``resource_id``."""


class UpdateSharedDocumentParams(ToolParams):
    content: str = Field(max_length=8_000)


class EmailContent(ToolParams):
    recipient: str
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8_000)

    @field_validator("recipient")
    @classmethod
    def _recipient(cls, value: str) -> str:
        return validate_email_address(value)

    def fields(self) -> dict[str, str]:
        return {"recipient": self.recipient, "subject": self.subject, "body": self.body}


class PrepareEmailParams(EmailContent):
    pass


class SendEmailParams(EmailContent):
    """The request must state exactly what will be sent."""


class ModifyEmailParams(ToolParams):
    recipient: str | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=8_000)

    @field_validator("recipient")
    @classmethod
    def _recipient(cls, value: str | None) -> str | None:
        return None if value is None else validate_email_address(value)

    @model_validator(mode="after")
    def _at_least_one(self) -> ModifyEmailParams:
        if not self.changes():
            raise ValueError("modify_email requires at least one of recipient, subject, body")
        return self

    def changes(self) -> dict[str, str]:
        return {k: v for k, v in (("recipient", self.recipient), ("subject", self.subject), ("body", self.body)) if v is not None}


class RequestApprovalParams(ToolParams):
    operation: str = Field(pattern=TOOL_NAME_PATTERN)


class AssignTaskParams(ToolParams):
    assignee: str = Field(pattern=ID_PATTERN)
    description: str = Field(min_length=1, max_length=1_000)
    depends_on_task_ids: list[str] = Field(default_factory=list, max_length=MAX_PARENTS)
    # Stage 4: tools the task needs. A task is never a permission grant: the policy blocks the assignment if
    # any listed tool is outside the assignee's role permissions.
    required_tools: list[str] = Field(default_factory=list, max_length=16)


class SendMessageParams(ToolParams):
    """Stage 4: an inter-agent message, sent through the gateway like every other side effect."""

    recipient: str = Field(pattern=ID_PATTERN)
    message_type: str = Field(pattern=r"^(TASK_ASSIGNMENT|INFORMATION|REQUEST|RESPONSE|STATUS_UPDATE|WARNING|TASK_COMPLETION)$")
    text: str = Field(min_length=1, max_length=2_000)
    parent_message_id: str | None = Field(default=None, pattern=ID_PATTERN)


class CompleteTaskParams(ToolParams):
    summary: str = Field(default="", max_length=2_000)
