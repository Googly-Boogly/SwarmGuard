"""Research questions are hypotheses, never predeclared conclusions.

The registry is versioned. Each laboratory experiment records the registry version it was created under in
its manifest, and reports render that version's text, so a reworded or retired question never changes a
historical report or breaks verification of an existing reproducibility bundle. Records created before
versioning carry no version and use ``rq-registry-1``, which also keeps the retired RQ4–RQ7.
"""
from __future__ import annotations
from typing import Any, Literal
from swarmguard.models.adversarial import StrictModel

QuestionStatus = Literal["NOT_EVALUATED", "EXPERIMENT_CONFIGURED", "EVALUATION_RUNNING", "RESULTS_AVAILABLE", "INCONCLUSIVE"]

LEGACY_REGISTRY_VERSION = "rq-registry-1"
REGISTRY_VERSION = "rq-registry-2"
REGISTRY_VERSIONS = (LEGACY_REGISTRY_VERSION, REGISTRY_VERSION)


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
    question: str | None = None
    what_we_are_testing: str | None = None
    experiment: str | None = None
    comparison: list[str] = []
    goal: str | None = None


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

_DEFAULT_CONTROLS = ["Exact dataset version", "Policy and ruleset versions",
                     "Model, prompt, aggregation and request budget", "Paired scenario identities"]
_DEFAULT_DATASET = ["Trusted labels isolated from inference", "Both safe and unsafe cases",
                    "Independent evaluation for generalization claims"]
_DEFAULT_CONFOUNDERS = ["Public same-author fixtures", "Dependent variants", "Synthetic policies",
                        "Adapter coverage", "Critical-only checkpoints"]
_ANALYSIS_SUFFIX = ". Report descriptive paired effects and failures; no inferential claims from deterministic repeats."

# rq-registry-2: RQ1–RQ3 restated by the research team (2026-11-07). RQ4–RQ7 are retired: they exist only in
# rq-registry-1, for rendering experiments created before this version, and new experiments cannot select them.
_DETAILED: dict[str, dict[str, Any]] = {
    "RQ1": {
        "title": "Global Workflow Monitoring",
        "question": "Can global workflow monitoring detect distributed approval laundering in autonomous multi-agent "
                    "systems more effectively than local action monitoring, while maintaining a low false-positive rate?",
        "hypothesis": "Global workflow monitoring improves detection recall for distributed approval-laundering "
                      "violations compared with local action monitoring, without an unacceptable increase in false positives.",
        "null_hypothesis": "Global workflow monitoring does not measurably improve detection over local action "
                           "monitoring under the declared conditions.",
        "what_we_are_testing": "Whether monitoring the entire workflow helps detect unsafe coordination that "
                               "individual-action monitors miss.",
        "experiment": "Compare Local and Global Monitors using identical multi-agent scenarios.",
        "comparison": ["Local Action Monitor", "Global Workflow Monitor"],
        "independent_variables": ["monitor_context"],
        "dependent_variables": ["recall", "precision", "false_positive_rate", "f1", "detection_latency"],
        "experimental_controls": ["Identical scenarios and recorded trajectories for both monitors",
                                  "One as-of snapshot per checkpoint for both windows", "Same RiskAnalyzer rules and ruleset version",
                                  *_DEFAULT_CONTROLS],
        "known_confounders": ["Information-access difference: the local window cannot see source records",
                              *_DEFAULT_CONFOUNDERS],
        "analysis_plan": "Matched local/global observations at the same cutoff",
        "goal": "Determine whether cross-agent visibility improves safety detection.",
    },
    "RQ2": {
        "title": "Ethical Diversity",
        "question": "Does an AI Ethics Council using virtue ethics, consequentialism, deontology, and care ethics detect "
                    "unsafe multi-agent behavior more effectively than an equivalent council of general-purpose safety monitors?",
        "hypothesis": "A council of four monitors specialized in virtue ethics, consequentialism, deontology and care ethics "
                      "detects unsafe multi-agent behavior more effectively than a council of four general-purpose safety monitors.",
        "null_hypothesis": "Philosophical specialization provides no measurable detection benefit over an equivalent "
                           "general-purpose council under the declared conditions.",
        "what_we_are_testing": "Whether specialized philosophical reasoning improves AI safety monitoring.",
        "experiment": "Compare a General Council with a Specialized Ethics Council. Both councils receive equivalent "
                      "information and use comparable models and evaluation budgets.",
        "comparison": ["General Council: four general-purpose LLM safety monitors",
                       "Specialized Ethics Council: four LLM monitors, each specializing in a different ethical framework"],
        "independent_variables": ["council_specialization"],
        "dependent_variables": ["recall", "precision", "false_positive_rate", "f1", "monitor_disagreement", "computational_cost"],
        "experimental_controls": ["Equivalent information for both councils", "Comparable models",
                                  "Equal evaluation budgets", "Same aggregation rule", *_DEFAULT_CONTROLS],
        "known_confounders": ["Prompt length and wording differ between councils",
                              "Mock council agreement is constructed, not evidence", *_DEFAULT_CONFOUNDERS],
        "analysis_plan": "Four-general versus four-specialized is the primary comparison; identical evidence and budgets",
        "goal": "Determine whether philosophical specialization provides measurable safety benefits.",
    },
    "RQ3": {
        "title": "Deterministic Enforcement",
        "question": "Can trusted deterministic authorization prevent prohibited operations in multi-agent systems even "
                    "when AI-based safety monitors fail to detect unsafe behavior?",
        "hypothesis": "The Capability Gateway prevents specified prohibited operations independently of AI monitor "
                      "recommendations, including cases the monitors fail to flag.",
        "null_hypothesis": "Deterministic authorization does not prevent the specified prohibited operations when "
                           "monitors fail, under the declared conditions.",
        "what_we_are_testing": "Whether SwarmGuard's Capability Gateway can prevent unauthorized operations "
                               "independently of AI monitor recommendations.",
        "experiment": "Run multi-agent scenarios in which agents propose prohibited operations, including cases where "
                      "monitoring fails to flag the risk.",
        "comparison": ["What the AI monitors detect", "What the deterministic Policy Engine authorizes",
                       "What operations actually execute"],
        "independent_variables": ["advisory_failure"],
        "dependent_variables": ["unauthorized_execution_rate", "blocked_unauthorized_requests",
                                "legitimate_operation_success_rate", "authorization_overhead"],
        "experimental_controls": ["Mandatory authorization retained in every configuration",
                                  "Both permitted and prohibited operations", *_DEFAULT_CONTROLS],
        "known_confounders": ["Only operations the policy specifies can be blocked; policy-compliant harms are out of scope",
                              *_DEFAULT_CONFOUNDERS],
        "analysis_plan": "Retain mandatory authorization; test permitted and prohibited operations. Record monitor "
                         "detection, gateway authorization and actual execution separately",
        "goal": "Demonstrate the effectiveness and limitations of deterministic security boundaries.",
    },
}


def _status(evaluations: list[dict[str, Any]]) -> QuestionStatus:
    status: QuestionStatus = "NOT_EVALUATED"
    if evaluations:
        status = "RESULTS_AVAILABLE" if any(e["status"] == "COMPLETED" for e in evaluations) else "INCONCLUSIVE"
        if any(e["status"] in ("QUEUED", "RUNNING") for e in evaluations):
            status = "EVALUATION_RUNNING"
    return status


def questions(experiments: list[dict[str, Any]] | None = None,
              registry_version: str = REGISTRY_VERSION) -> list[dict[str, Any]]:
    if registry_version not in REGISTRY_VERSIONS:
        raise ValueError("unknown research-question registry version")
    result = []
    for n, (title, hypothesis, variable, metrics, plan) in enumerate(_QUESTIONS, 1):
        qid = f"RQ{n}"
        evaluations = [e for e in experiments or [] if e["config"]["question"] == qid]
        detail = _DETAILED.get(qid) if registry_version == REGISTRY_VERSION else None
        if registry_version == REGISTRY_VERSION and detail is None:
            continue
        if detail is None:
            q = ResearchQuestion(research_question_id=qid, title=title, hypothesis=hypothesis + ".",
                null_hypothesis="No improvement or transfer under the declared conditions.", independent_variables=[variable],
                dependent_variables=metrics.split(","), experimental_controls=list(_DEFAULT_CONTROLS),
                dataset_requirements=list(_DEFAULT_DATASET), known_confounders=list(_DEFAULT_CONFOUNDERS),
                analysis_plan=plan + _ANALYSIS_SUFFIX, status=_status(evaluations))
        else:
            q = ResearchQuestion(research_question_id=qid, dataset_requirements=list(_DEFAULT_DATASET),
                status=_status(evaluations), **{**detail, "analysis_plan": detail["analysis_plan"] + _ANALYSIS_SUFFIX})
        result.append({**q.model_dump(), "registry_version": registry_version,
                       "experiment_ids": [e["experiment_id"] for e in evaluations],
                       "evaluation_metrics": list(q.dependent_variables)})
    return result


def active_question_ids() -> tuple[str, ...]:
    """Questions a new experiment may select."""
    return tuple(_DETAILED)


def question_for(record: dict[str, Any]) -> dict[str, Any]:
    """The question text an experiment was created under (legacy records predate versioning)."""
    version = record["manifest"].get("question_registry_version", LEGACY_REGISTRY_VERSION)
    return next(q for q in questions(registry_version=version)
                if q["research_question_id"] == record["config"]["question"])
