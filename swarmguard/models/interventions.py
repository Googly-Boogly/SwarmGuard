"""Safety interventions issued by the trusted runtime (never by an LLM)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InterventionType(StrEnum):
    NONE = "NONE"
    WARNING = "WARNING"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    PAUSE_WORKFLOW = "PAUSE_WORKFLOW"
    TRUSTED_POLICY_BLOCK = "TRUSTED_POLICY_BLOCK"


class InterventionMode(StrEnum):
    MONITORING_ONLY = "MONITORING_ONLY"      # assessments recorded, agents unaffected
    WARNING = "WARNING"                      # alerts delivered to the acting agent as system messages
    HUMAN_OVERSIGHT = "HUMAN_OVERSIGHT"      # trusted guard escalates alerting protected sends; pause on repeats
    POLICY_ENFORCEMENT = "POLICY_ENFORCEMENT"  # explicit deterministic workflow policy (recipient scope)


class InterventionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    intervention_id: str
    workflow_id: str
    intervention_type: InterventionType
    triggering_event_id: str | None
    triggering_action_id: str | None = None
    target_agent_id: str | None = None
    reason: str
    status: str = Field(default="ISSUED", description="ISSUED | APPLIED | DECLINED_BY_OPERATOR")
    source: str = Field(description="Trusted component that issued it")
    created_at: datetime
