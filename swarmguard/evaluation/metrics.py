"""Detection metrics. Zero denominators return ``None`` explicitly (never 0 or NaN)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[bool, bool]]) -> ConfusionMatrix:
        """``pairs`` = (ground_truth_harmful, predicted_flagged)."""
        tp = fp = tn = fn = 0
        for harmful, flagged in pairs:
            if harmful and flagged:
                tp += 1
            elif harmful:
                fn += 1
            elif flagged:
                fp += 1
            else:
                tn += 1
        return cls(tp, fp, tn, fn)

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def precision(m: ConfusionMatrix) -> float | None:
    return _ratio(m.tp, m.tp + m.fp)


def recall(m: ConfusionMatrix) -> float | None:
    return _ratio(m.tp, m.tp + m.fn)


def true_positive_rate(m: ConfusionMatrix) -> float | None:
    return recall(m)


def false_positive_rate(m: ConfusionMatrix) -> float | None:
    return _ratio(m.fp, m.fp + m.tn)


def summarize(m: ConfusionMatrix) -> dict[str, float | int | None]:
    return {"tp": m.tp, "fp": m.fp, "tn": m.tn, "fn": m.fn, "precision": precision(m), "recall": recall(m),
            "true_positive_rate": true_positive_rate(m), "false_positive_rate": false_positive_rate(m)}


# --------------------------------------------------------------------------- Stage 2 additions


def f1_score(m: ConfusionMatrix) -> float | None:
    p, r = precision(m), recall(m)
    if p is None or r is None or p + r == 0:
        return None
    return 2 * p * r / (p + r)


def accuracy(m: ConfusionMatrix) -> float | None:
    return _ratio(m.tp + m.tn, m.total)


def detection_latency(first_alert_steps: Iterable[int | None], critical_steps: Iterable[int]) -> dict[str, float | int | None]:
    """Event-order latency for unsafe scenarios, in workflow action steps.

    ``lead`` = critical step - first alert step (positive: alert raised *before* the critical
    action; 0: at it; negative: after it). Undetected scenarios are counted, not averaged in.
    """
    firsts, crits = list(first_alert_steps), list(critical_steps)
    leads = [c - f for f, c in zip(firsts, crits, strict=True) if f is not None]
    n = len(crits)
    return {
        "detected": len(leads),
        "undetected": n - len(leads),
        "mean_lead_steps": (sum(leads) / len(leads)) if leads else None,
        "min_lead_steps": min(leads) if leads else None,
        "max_lead_steps": max(leads) if leads else None,
    }


def prevention_rate(prohibited_outcome_occurred: Iterable[bool]) -> float | None:
    """Fraction of unsafe scenarios whose prohibited outcome did NOT occur. None if there are none."""
    items = list(prohibited_outcome_occurred)
    return _ratio(sum(1 for o in items if not o), len(items))


def summarize_detection(m: ConfusionMatrix) -> dict[str, float | int | None]:
    return {**summarize(m), "f1": f1_score(m), "accuracy": accuracy(m), "n": m.total}


def fmt(value: float | int | None, digits: int = 3) -> str:
    """Display helper: undefined metrics are shown as 'undefined', never as 0 or 1."""
    if value is None:
        return "undefined"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)
