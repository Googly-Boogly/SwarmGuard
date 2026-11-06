"""Shared assessment enumerations (risk, uncertainty, recommendation).

Defined here so that both the ethics models (``assessments.py``) and the Stage 2
workflow-risk models (``risk.py``) can use them without an import cycle.
``assessments.py`` re-exports them, so existing imports keep working.
"""

from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _RISK_ORDER[self]


_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


class Uncertainty(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Recommendation(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"
