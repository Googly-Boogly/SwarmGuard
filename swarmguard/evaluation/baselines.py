"""Monitor conditions planned for the research comparisons (docs/RESEARCH_PLAN.md).

Stage 1 defines the conditions and the context restriction only. The general-purpose
and specialized LLM monitors are Stage 2 work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from swarmguard.models.assessments import EthicalFramework, WorkflowContext
from swarmguard.models.events import EventType


class ContextScope(StrEnum):
    ACTION_ONLY = "ACTION_ONLY"
    FULL_WORKFLOW = "FULL_WORKFLOW"


@dataclass(frozen=True)
class MonitorCondition:
    condition_id: str
    description: str
    scope: ContextScope
    monitor_count: int
    frameworks: tuple[EthicalFramework, ...] = ()
    deterministic_enforcement: bool = False
    implemented: bool = False


CONDITIONS: tuple[MonitorCondition, ...] = (
    MonitorCondition("single_action_monitor", "One general-purpose monitor; sees only the proposed action.",
                     ContextScope.ACTION_ONLY, 1),
    MonitorCondition("workflow_monitor", "One general-purpose monitor; sees the complete workflow.",
                     ContextScope.FULL_WORKFLOW, 1),
    MonitorCondition("general_purpose_x4", "Four general-purpose safety monitors; complete workflow.",
                     ContextScope.FULL_WORKFLOW, 4),
    MonitorCondition("specialized_ethics_x4", "Virtue, consequentialist, deontological, and care monitors.",
                     ContextScope.FULL_WORKFLOW, 4, tuple(EthicalFramework)),
    MonitorCondition("workflow_plus_ethics", "Workflow monitoring + four ethical monitors + deterministic enforcement.",
                     ContextScope.FULL_WORKFLOW, 4, tuple(EthicalFramework), deterministic_enforcement=True),
    MonitorCondition("deterministic_only", "Policy engine and approval binding only (RQ3 reference).",
                     ContextScope.FULL_WORKFLOW, 0, deterministic_enforcement=True, implemented=True),
)


def condition(condition_id: str) -> MonitorCondition:
    return next(c for c in CONDITIONS if c.condition_id == condition_id)


def restrict_context(context: WorkflowContext, scope: ContextScope) -> WorkflowContext:
    """Return the view of the context a monitor in ``scope`` is allowed to see."""
    if scope is ContextScope.FULL_WORKFLOW:
        return context
    own = [e for e in context.history
           if e.action_id == context.action.action_id and e.event_type is EventType.ACTION_PROPOSED]
    return context.model_copy(update={"history": own, "related_actions": [], "resource_history": [],
                                      "resource_versions": [], "approvals": [], "scope": "ACTION_ONLY"})
