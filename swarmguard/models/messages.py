"""Inter-agent messages. Message content is UNTRUSTED data: it never changes permissions or approvals."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MAX_MESSAGE_CHARS = 2_000


class MessageType(StrEnum):
    TASK_ASSIGNMENT = "TASK_ASSIGNMENT"
    INFORMATION = "INFORMATION"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    STATUS_UPDATE = "STATUS_UPDATE"
    WARNING = "WARNING"
    TASK_COMPLETION = "TASK_COMPLETION"


class AgentMessage(BaseModel):
    """A persisted message. ``sender_id`` is the gateway-authenticated identity, or a ``swarmguard.*``
    trusted component for system messages (``is_system``). Agents cannot set ``is_system``."""

    model_config = ConfigDict(frozen=True)

    message_id: str
    workflow_id: str
    sender_id: str
    recipient_id: str
    message_type: MessageType
    text: str = Field(max_length=MAX_MESSAGE_CHARS)
    parent_message_id: str | None = None
    action_id: str | None = Field(default=None, description="Gateway action that sent it (None for system messages).")
    event_id: str | None = None
    is_system: bool = False
    created_at: datetime
