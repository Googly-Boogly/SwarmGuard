"""Synthetic field labels and explicit provenance. No semantic paraphrase analysis."""
from __future__ import annotations
from enum import StrEnum
from typing import Any
from swarmguard.models.risk import MonitorContext


class InformationLabel(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    PROTECTED_SYNTHETIC = "PROTECTED_SYNTHETIC"


def information_flow(ctx: MonitorContext) -> list[dict[str, Any]]:
    from swarmguard.security.pattern_detector import detect_all
    detection = detect_all(ctx)
    out = []
    protected = {f for inv in ctx.invariants for f in inv.protected_fields}
    for flow in detection.flows:
        out.append({**flow.model_dump(mode="json"), "field_labels": {
            field: (InformationLabel.PROTECTED_SYNTHETIC if field in protected else InformationLabel.CONFIDENTIAL).value
            for field in [*flow.protected_fields, *flow.quasi_identifier_fields]},
            "restricted_destination": not flow.recipient_authorized,
            "provenance": [h.model_dump(mode="json") for h in flow.hops],
            "method": "exact synthetic field matching plus recorded resource dependencies"})
    return out
