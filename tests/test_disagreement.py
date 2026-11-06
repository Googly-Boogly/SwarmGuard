"""Disagreement analysis."""

from __future__ import annotations

from swarmguard.ethics.disagreement import analyze, categorize, disagreement_rate
from swarmguard.models.assessments import EthicalAssessment, EthicalFramework as F


def a(mid, fw, rec, risk, concerns=(), evidence=()):
    return EthicalAssessment(framework=fw, action_id="action_1", risk_level=risk, uncertainty="LOW",
                             recommendation=rec, explanation="x", monitor_id=mid, concerns=list(concerns),
                             evidence_event_ids=list(evidence))


def test_recommendation_risk_concern_and_evidence_disagreement():
    rows = [a("v", F.VIRTUE_ETHICS, "ESCALATE", "MEDIUM", ["Misleading recipient"], ["e1", "e2"]),
            a("c", F.CONSEQUENTIALISM, "BLOCK", "HIGH", ["Harm to customer"], ["e1"]),
            a("d", F.DEONTOLOGY, "BLOCK", "CRITICAL", ["No consent for disclosure"], ["e1", "e3"]),
            a("k", F.CARE_ETHICS, "ESCALATE", "MEDIUM", ["Breach of trust"], [])]
    r = analyze("action_1", rows, ["v", "c", "d", "k", "missing_one"])
    assert r.recommendations == {"v": "ESCALATE", "c": "BLOCK", "d": "BLOCK", "k": "ESCALATE"}
    assert r.distinct_recommendations == 2 and r.disagreement_detected and r.recommendation_disagreement == 0.5
    assert r.risk_level_spread == 2 and r.missing_monitors == ["missing_one"] and r.n_assessed == 4
    assert r.pairwise_recommendation_agreement == 2 / 6
    assert "HONESTY_DECEPTION" in r.categories_unique_to["v"] and "WELLBEING_TRUST" in r.concern_categories["k"]
    assert 0 < r.evidence_overlap_jaccard < 1


def test_agreement_and_rates():
    same = [a(m, F.GENERAL_PURPOSE, "ALLOW", "LOW") for m in "wxyz"]
    r = analyze("action_1", same, list("wxyz"))
    assert not r.disagreement_detected and r.recommendation_disagreement == 0 and r.evidence_overlap_jaccard is None
    split = analyze("action_1", [same[0], a("q", F.GENERAL_PURPOSE, "BLOCK", "HIGH")], ["w", "q"])
    assert disagreement_rate([r, split]) == 0.5 and disagreement_rate([]) is None
    assert categorize("unrelated remark") == ["OTHER"]
