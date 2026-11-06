"""Council aggregation strategies (advisory only; never a substitute for deterministic authorization).

Restrictiveness ordering used for ties and escalation: BLOCK > ESCALATE > ALLOW. It is a heuristic
for advisory recommendations.

* ``UNANIMITY`` (Stage 1 default): ALLOW only if every assessment is ALLOW; BLOCK only if every
  assessment is BLOCK; otherwise ESCALATE.
* ``MAJORITY``: the most frequent recommendation. Ties go to the most restrictive tied option.
* ``CONSERVATIVE``: the most restrictive recommendation present.
* ``MAJORITY_WITH_ESCALATION``: the majority, unless disagreement (1 − modal share) exceeds
  ``disagreement_threshold``; then at least ESCALATE. With 4 monitors and threshold 0.25, a 3–1 split
  keeps the majority and a 2–2 split escalates.

Incomplete councils (a failed or missing required monitor): a missing assessment never counts as
ALLOW. ``IncompletePolicy.ESCALATE`` (default) returns at least ESCALATE.
``IncompletePolicy.AGGREGATE_AVAILABLE`` aggregates the available votes only if at least ``quorum``
are present, and otherwise escalates.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from swarmguard.models.levels import Recommendation

RESTRICTIVENESS = {Recommendation.ALLOW: 0, Recommendation.ESCALATE: 1, Recommendation.BLOCK: 2}


def most_restrictive(recs: Sequence[Recommendation]) -> Recommendation:
    return max(recs, key=lambda r: RESTRICTIVENESS[r])


class AggregationMethod(StrEnum):
    UNANIMITY = "UNANIMITY"
    MAJORITY = "MAJORITY"
    CONSERVATIVE = "CONSERVATIVE"
    MAJORITY_WITH_ESCALATION = "MAJORITY_WITH_ESCALATION"


class IncompletePolicy(StrEnum):
    ESCALATE = "ESCALATE"
    AGGREGATE_AVAILABLE = "AGGREGATE_AVAILABLE"


@dataclass(frozen=True)
class AggregationOutcome:
    recommendation: Recommendation
    method: AggregationMethod
    incomplete: bool
    disagreement: float | None
    rationale: str


@dataclass(frozen=True)
class Aggregator:
    method: AggregationMethod = AggregationMethod.UNANIMITY
    incomplete_policy: IncompletePolicy = IncompletePolicy.ESCALATE
    disagreement_threshold: float = 0.25
    quorum: int = 3

    @property
    def short_name(self) -> str:
        return self.method.value.lower()

    def describe(self) -> dict[str, object]:
        return {"method": self.method.value, "incomplete_policy": self.incomplete_policy.value,
                "disagreement_threshold": self.disagreement_threshold, "quorum": self.quorum,
                "ordering": "BLOCK > ESCALATE > ALLOW"}

    def _over_available(self, recs: Sequence[Recommendation]) -> tuple[Recommendation, float]:
        counts = Counter(recs)
        top = max(counts.values())
        disagreement = 1 - top / len(recs)
        majority = most_restrictive([r for r, c in counts.items() if c == top])
        if self.method is AggregationMethod.UNANIMITY:
            if set(recs) == {Recommendation.ALLOW}:
                return Recommendation.ALLOW, disagreement
            if set(recs) == {Recommendation.BLOCK}:
                return Recommendation.BLOCK, disagreement
            return Recommendation.ESCALATE, disagreement
        if self.method is AggregationMethod.MAJORITY:
            return majority, disagreement
        if self.method is AggregationMethod.CONSERVATIVE:
            return most_restrictive(recs), disagreement
        if disagreement > self.disagreement_threshold:
            return most_restrictive([majority, Recommendation.ESCALATE]), disagreement
        return majority, disagreement

    def aggregate(self, recs: Sequence[Recommendation], *, incomplete: bool) -> AggregationOutcome:
        if not recs:
            return AggregationOutcome(Recommendation.ESCALATE, self.method, True, None,
                                      "no valid assessments: escalate (missing never counts as ALLOW)")
        rec, disagreement = self._over_available(list(recs))
        if not incomplete:
            return AggregationOutcome(rec, self.method, False, disagreement, f"{self.method.value} over {len(recs)}")
        if self.method is AggregationMethod.UNANIMITY or self.incomplete_policy is IncompletePolicy.ESCALATE \
                or len(recs) < self.quorum:
            return AggregationOutcome(most_restrictive([rec, Recommendation.ESCALATE]), self.method, True,
                                      disagreement, "incomplete council: at least ESCALATE")
        return AggregationOutcome(rec, self.method, True, disagreement,
                                  f"incomplete council: {self.method.value} over {len(recs)} available (quorum "
                                  f"{self.quorum})")
