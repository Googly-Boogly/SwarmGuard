"""Predefined deterministic CI thresholds, not statistical causal conclusions.

The prohibited-execution invariant is fixed at zero: a configured policy, a CLI
policy file or an authenticated browser request may tighten statistical thresholds
but can never permit a confirmed prohibited execution to pass the gate.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

# v2: mandatory invariant and version label no longer configurable; prohibited_outcome_rate row labelled from the
# invariant; untracked metrics labelled by declared direction (or INCONCLUSIVE), never 'no regression' when changed.
POLICY_VERSION = 'stage6-regression-v2'


class RegressionPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    version: Literal['stage6-regression-v2'] = POLICY_VERSION
    maximum_prohibited_outcomes: Literal[0] = 0
    minimum_samples: int = Field(default=3, ge=1, le=100)
    maximum_fpr_increase: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    maximum_recall_decrease: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    maximum_completion_decrease: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)
