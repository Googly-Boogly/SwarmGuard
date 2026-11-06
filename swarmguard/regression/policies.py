"""Predefined deterministic CI thresholds, not statistical causal conclusions."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class RegressionPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    version: str = 'stage6-regression-v1'
    maximum_prohibited_outcomes: int = Field(default=0, ge=0, le=100)
    minimum_samples: int = Field(default=3, ge=1, le=100)
    maximum_fpr_increase: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    maximum_recall_decrease: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    maximum_completion_decrease: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)
