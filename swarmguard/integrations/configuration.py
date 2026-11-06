"""Host-only, immutable integration settings; no credentials or policy overrides."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Framework = Literal['custom', 'crewai', 'langgraph', 'autogen']


class IntegrationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    framework: Framework = 'custom'
    mode: Literal['ENFORCED', 'OBSERVABILITY'] = 'ENFORCED'
    monitoring: Literal['OBSERVE', 'WARN', 'ESCALATE', 'ENFORCE'] = 'OBSERVE'
    ethics_council: bool = False
    collect_monitoring: bool = True
    max_proposals: int = Field(default=60, ge=1, le=60)
    max_events: int = Field(default=500, ge=1, le=1000)
    max_runtime_seconds: int = Field(default=60, ge=1, le=300)
