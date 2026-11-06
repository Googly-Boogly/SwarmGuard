"""Reported framework observations are never execution proof or authorization identity."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from swarmguard.models.actions import ID_PATTERN, _check_parameters_size

EventKind = Literal['AGENT_CREATED','AGENT_ACTIVITY','TASK_ASSIGNED','MESSAGE','TOOL_CALL_PROPOSED',
    'TOOL_EXECUTED','TOOL_FAILED','WORKFLOW_COMPLETED','WORKFLOW_ERROR','APPROVAL_REQUESTED','AUTHORIZATION_DECISION']


class FrameworkEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    event_type: EventKind
    external_event_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=128)
    reported_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('metadata')
    @classmethod
    def bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_parameters_size(value)


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    event_id: str
    integration_id: str
    framework: str
    workflow_id: str = Field(pattern=ID_PATTERN)
    agent_id: str
    event_type: EventKind
    timestamp: datetime
    reported_at: datetime | None = None
    source: Literal['FRAMEWORK_REPORTED', 'GATEWAY_VERIFIED', 'HOST_REGISTERED']
    action_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    parent_action_ids: list[str] = Field(default_factory=list)
    external_event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
