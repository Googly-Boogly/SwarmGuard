"""Task views. Tasks are versioned workspace resources created by the gateway's ``assign_task`` tool;
``TaskRecord`` is the trusted runtime view, including the lifecycle state."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    PENDING = "PENDING"      # dependencies not yet completed
    READY = "READY"          # all dependencies completed
    RUNNING = "RUNNING"      # assignee has acted on it
    WAITING = "WAITING"      # assignee is waiting (e.g. on an approval or information)
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class TaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    workflow_id: str
    assigned_by: str
    assigned_to: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    status: TaskStatus
    version: int
    summary: str = ""
