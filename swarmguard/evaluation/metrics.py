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
