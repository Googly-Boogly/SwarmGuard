"""Research questions are hypotheses, never predeclared conclusions."""
from __future__ import annotations
from typing import Any, Literal
from swarmguard.models.adversarial import StrictModel

QuestionStatus = Literal["NOT_EVALUATED", "EXPERIMENT_CONFIGURED", "EVALUATION_RUNNING", "RESULTS_AVAILABLE", "INCONCLUSIVE"]


class ResearchQuestion(StrictModel):
    research_question_id: str
    title: str
    hypothesis: str
    null_hypothesis: str
    independent_variables: list[str]
    dependent_variables: list[str]
    experimental_controls: list[str]
    dataset_requirements: list[str]
    known_confounders: list[str]
    analysis_plan: str
    status: QuestionStatus = "NOT_EVALUATED"


_QUESTIONS = [
    ("Global Workflow Monitoring", "Global context improves workflow-risk detection", "monitor_context",
     "recall,false_positive_rate,detection_lead_events", "Matched local/global observations at the same cutoff"),
    ("Ethical Diversity", "Four specialized perspectives improve detection over four general monitors", "council_specialization",
     "recall,precision,f1,disagreement,cost", "Four-general versus four-specialized is the primary comparison; identical evidence and budgets"),
    ("Deterministic Enforcement", "The gateway prevents specified unauthorized operations despite monitor errors", "advisory_failure",
     "prohibited_outcomes,legitimate_completion", "Retain mandatory authorization; test permitted and prohibited operations"),
    ("Autonomous Agent Behavior", "Workflow monitors detect unsafe autonomous coordination", "agent_policy",
     "recall,false_positive_rate,model_failure_rate", "Mock policies validate plumbing; live behavior needs an explicitly authorized independent study"),
    ("Adversarial Robustness", "Monitoring remains effective under adversarial evidence perturbations", "observation_transform",
     "recall,false_positive_rate,evidence_validity", "Pair copied observations; preserve mandatory evidence and original artifacts"),
    ("Framework Generalization", "Monitoring performance transfers across supported framework wrappers", "agent_framework",
     "event_coverage,recall,legitimate_completion,latency", "Compare the same trusted fixtures; separate missing adapter events from safety outcomes"),
    ("Safety and Utility", "Oversight reduces prohibited outcomes while preserving legitimate completion", "intervention",
     "prevention_rate,legitimate_completion,latency,cost", "Compare policy/local/global interventions in isolated synthetic environments"),
]


def questions(experiments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    result = []
    for n, (title, hypothesis, variable, metrics, plan) in enumerate(_QUESTIONS, 1):
        qid = f"RQ{n}"
        evaluations = [e for e in experiments or [] if e["config"]["question"] == qid]
        status: QuestionStatus = "NOT_EVALUATED"
        if evaluations:
            status = "RESULTS_AVAILABLE" if any(e["status"] == "COMPLETED" for e in evaluations) else "INCONCLUSIVE"
            if any(e["status"] in ("QUEUED", "RUNNING") for e in evaluations):
                status = "EVALUATION_RUNNING"
        q = ResearchQuestion(research_question_id=qid, title=title, hypothesis=hypothesis + ".",
            null_hypothesis="No improvement or transfer under the declared conditions.", independent_variables=[variable],
            dependent_variables=metrics.split(","), experimental_controls=["Exact dataset version", "Policy and ruleset versions",
                "Model, prompt, aggregation and request budget", "Paired scenario identities"],
            dataset_requirements=["Trusted labels isolated from inference", "Both safe and unsafe cases", "Independent evaluation for generalization claims"],
            known_confounders=["Public same-author fixtures", "Dependent variants", "Synthetic policies", "Adapter coverage", "Critical-only checkpoints"],
            analysis_plan=plan + ". Report descriptive paired effects and failures; no inferential claims from deterministic repeats.", status=status)
        result.append({**q.model_dump(), "experiment_ids": [e["experiment_id"] for e in evaluations],
                       "evaluation_metrics": metrics.split(",")})
    return result
