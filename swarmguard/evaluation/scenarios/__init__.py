"""Scenario library.

* ``stage1``: the two Stage 1 acceptance scenarios (re-exported here for compatibility).
* Stage 2 scenarios implement ``base.Scenario``; ``STAGE2_SCENARIOS`` lists them in a fixed order.
"""

from swarmguard.evaluation.scenarios.stage1 import (
    APPROVAL_MISMATCH_SCENARIO,
    APPROVED_EMAIL,
    AUTHORIZED_EMAIL_SCENARIO,
    DEMO_SCENARIOS,
    LAUNDERED_RECIPIENT,
    GroundTruthLabel,
    ScenarioDefinition,
    ScenarioOutcome,
    ScenarioStepFailed,
    run_approval_laundering,
    run_authorized_email,
)

__all__ = [
    "APPROVAL_MISMATCH_SCENARIO", "APPROVED_EMAIL", "AUTHORIZED_EMAIL_SCENARIO", "DEMO_SCENARIOS",
    "LAUNDERED_RECIPIENT", "GroundTruthLabel", "ScenarioDefinition", "ScenarioOutcome", "ScenarioStepFailed",
    "run_approval_laundering", "run_authorized_email", "stage2_scenarios",
]


def stage2_scenarios():  # noqa: ANN201 - list[Scenario]; imported lazily to avoid import cycles
    from swarmguard.evaluation.scenarios.approval_mismatch import ApprovalMismatchScenario, ExpiredApprovalScenario
    from swarmguard.evaluation.scenarios.cross_agent_disclosure import (
        PurposeViolationScenario,
        TransformedDisclosureScenario,
    )
    from swarmguard.evaluation.scenarios.distributed_approval import (
        DistributedApprovalScenario,
        ReapprovalLaunderingScenario,
    )
    from swarmguard.evaluation.scenarios.legitimate_workflows import (
        LegitimateAccountUpdateScenario,
        LegitimateCollaborationScenario,
        LegitimateSharedModificationScenario,
        RejectedThenApprovedScenario,
        StaleAuthorizationViewScenario,
    )
    from swarmguard.evaluation.scenarios.shared_memory import SharedMemoryManipulationScenario
    from swarmguard.evaluation.scenarios.task_decomposition import (
        LegitimateDecompositionScenario,
        TaskDecompositionScenario,
    )

    return [
        ApprovalMismatchScenario(), DistributedApprovalScenario(), ReapprovalLaunderingScenario(),
        SharedMemoryManipulationScenario(), TaskDecompositionScenario(), PurposeViolationScenario(),
        TransformedDisclosureScenario(), ExpiredApprovalScenario(),
        LegitimateCollaborationScenario(), LegitimateAccountUpdateScenario(), LegitimateSharedModificationScenario(),
        LegitimateDecompositionScenario(), RejectedThenApprovedScenario(), StaleAuthorizationViewScenario(),
    ]
