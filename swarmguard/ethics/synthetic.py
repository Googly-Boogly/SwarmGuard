"""Synthetic (mock-mode) responder: Stage 2 deterministic rules rendered in the LLM response schema.

This is NOT ethical reasoning and NOT an LLM. It ignores the system prompt entirely: it parses
the untrusted JSON evidence, runs ``RiskAnalyzer`` on the observation, and returns an
``EthicalJudgment``-shaped dict. Every framework and the general baseline therefore get the
*same* answer for the same evidence. In mock mode, specialized and general councils are
identical by construction, which validates the pipeline (schemas, evidence checks, aggregation,
persistence, metrics) and says nothing about RQ2.
"""

from __future__ import annotations

import json
from typing import Any

from swarmguard.models.levels import Recommendation, RiskLevel, Uncertainty
from swarmguard.models.risk import MonitorContext
from swarmguard.security.risk_analyzer import RiskAnalyzer

_ANALYZER = RiskAnalyzer()


def rules_responder(system_prompt: str, user_content: str, tags: dict[str, str]) -> dict[str, Any]:
    payload = json.loads(user_content)
    data = payload.get("untrusted_workflow_data", {})
    if "observation" not in data:
        return {"risk_level": RiskLevel.MEDIUM.value, "recommendation": Recommendation.ESCALATE.value,
                "uncertainty": Uncertainty.HIGH.value, "concerns": ["No as-of workflow observation was supplied."],
                "evidence_event_ids": [], "legitimate_interpretations": [], "insufficient_evidence": True,
                "explanation": "Synthetic rules responder: insufficient structured evidence."}
    ctx = MonitorContext.model_validate(data["observation"])
    a = _ANALYZER.assess(ctx, monitor_id="synthetic_rules")
    concerns = [f.explanation for f in a.findings if f.actionable] or [
        f.explanation for f in a.findings if f.severity.rank >= RiskLevel.MEDIUM.rank]
    return {
        "risk_level": a.risk_level.value, "recommendation": a.recommendation.value,
        "uncertainty": a.uncertainty.value, "concerns": [c[:300] for c in concerns[:20]],
        "evidence_event_ids": a.evidence_event_ids, "legitimate_interpretations": [],
        "insufficient_evidence": bool(a.missing_information),
        "explanation": f"Synthetic rules responder ({a.ruleset_version}): {a.explanation}"[:4000],
    }
