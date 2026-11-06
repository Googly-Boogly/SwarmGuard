"""Ethics-monitor interface.

Every monitor (mock in Stage 1, LLM-backed in Stage 2) implements
``EthicsMonitor.evaluate(action, workflow_context) -> EthicalAssessment`` and
receives the identical ``WorkflowContext``. Monitors are advisory only.

Stage 3 adds ``LLMEthicsMonitor`` (bottom of this module), the shared LLM-backed implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import ValidationError

from swarmguard.models.actions import ActionRequest
from swarmguard.models.assessments import (
    EthicalAssessment,
    EthicalFramework,
    Recommendation,
    RiskLevel,
    Uncertainty,
    WorkflowContext,
)
from swarmguard.models.events import EventType, WorkflowEvent
from swarmguard.ethics.prompts import (
    PROMPT_VERSION,
    EthicalJudgment,
    citable_event_ids,
    prompt_hash,
    system_prompt,
    user_content,
)
from swarmguard.llm.base import LLMProvider, ProviderCallError

SYNTHETIC_PREFIX = "[SYNTHETIC MOCK response, not LLM reasoning]"


@dataclass(frozen=True)
class FrameworkProfile:
    """What a framework attends to. Stage 2 will turn this into monitor prompts."""

    framework: EthicalFramework
    title: str
    focus: tuple[str, ...]
    guiding_question: str


class EthicsMonitor(ABC):
    framework: EthicalFramework
    monitor_id: str

    @abstractmethod
    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        """Return a structured, advisory assessment. Must not have side effects."""


class MockEthicsMonitor(EthicsMonitor):
    """Base for Stage 1 placeholders.

    MOCK: these monitors apply a few fixed, hand-written keyword/event rules so
    the council pipeline can be exercised end to end. They perform no ethical
    reasoning, are not LLM outputs, and must not be reported as research
    results.
    """

    profile: FrameworkProfile

    def _assessment(
        self,
        action: ActionRequest,
        *,
        risk: RiskLevel,
        recommendation: Recommendation,
        concerns: list[str],
        evidence_event_ids: list[str],
        explanation: str,
        uncertainty: Uncertainty = Uncertainty.HIGH,
    ) -> EthicalAssessment:
        return EthicalAssessment(
            framework=self.framework,
            action_id=action.action_id,
            risk_level=risk,
            concerns=concerns,
            evidence_event_ids=evidence_event_ids,
            uncertainty=uncertainty,
            recommendation=recommendation,
            explanation=f"[MOCK heuristic, not an LLM assessment] {explanation}",
            monitor_id=self.monitor_id,
            is_mock=True,
        )

    def _no_signal(self, action: ActionRequest) -> EthicalAssessment:
        return self._assessment(
            action,
            risk=RiskLevel.LOW,
            recommendation=Recommendation.ALLOW,
            concerns=[],
            evidence_event_ids=[],
            explanation="No mock heuristic signal matched.",
        )


# --------------------------------------------------------------------------- helpers for mocks


def events_of(context: WorkflowContext, *types: EventType) -> list[WorkflowEvent]:
    return [e for e in context.history if e.event_type in types]


def cited_approval_events(context: WorkflowContext, *types: EventType) -> list[WorkflowEvent]:
    approval_id = context.action.approval_id
    return [e for e in events_of(context, *types) if approval_id and e.approval_id == approval_id]


def modifications_after_approval(context: WorkflowContext) -> list[tuple[WorkflowEvent, WorkflowEvent]]:
    """(APPROVAL_GRANTED, later RESOURCE_MODIFIED) pairs on the resource the cited approval covers.

    Uses explicit version numbers on events, not timestamps: a modification whose
    ``resource_version_before`` is >= the approved version came after the approval.
    """
    pairs = []
    for grant in cited_approval_events(context, EventType.APPROVAL_GRANTED):
        for mod in events_of(context, EventType.RESOURCE_MODIFIED):
            if (mod.resource_id == grant.resource_id and mod.resource_version_before is not None
                    and grant.resource_version_after is not None
                    and mod.resource_version_before >= grant.resource_version_after):
                pairs.append((grant, mod))
    return pairs


# =========================================================================== Stage 3: LLM-backed monitors


class LLMEthicsMonitor(EthicsMonitor):
    """Shared implementation of every Stage 3 LLM monitor (four frameworks plus the general baseline).

    Every subclass uses the same provider interface, input serialization, output schema, token
    limit and sampling settings. Only ``framework`` (and therefore the system prompt's perspective
    block) differs. The monitor:

    * builds the trusted system prompt from constants and the untrusted user JSON from the context,
    * calls the provider once (retries and budget live in the provider stack),
    * raises ``ProviderCallError`` instead of returning anything when the call fails or the output is
      unusable, so the council records a failure and never fabricates an assessment,
    * rejects any assessment citing an event ID that was not in its context (``INVALID_EVIDENCE``).
    """

    framework: EthicalFramework

    def __init__(self, provider: LLMProvider, *, monitor_id: str | None = None, max_output_tokens: int = 800,
                 temperature: float | None = None, seed: int | None = None) -> None:
        self._provider = provider
        self.monitor_id = monitor_id or f"llm_{self.framework.value}"
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.seed = seed
        self._counter = 0

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def configuration(self) -> dict[str, object]:
        return {"monitor_id": self.monitor_id, "framework": self.framework.value, "provider": self._provider.name,
                "model": self._provider.model, "is_synthetic": self._provider.is_synthetic,
                "prompt_version": PROMPT_VERSION, "prompt_hash": prompt_hash(self.framework),
                "max_output_tokens": self.max_output_tokens,
                "temperature": self.temperature if self.temperature is not None else "provider default",
                "seed": self.seed}

    async def evaluate(self, action: ActionRequest, workflow_context: WorkflowContext) -> EthicalAssessment:
        self._counter += 1
        request_id = f"{self.monitor_id}:{action.action_id}:{self._counter}"
        result = await self._provider.generate_structured(
            system_prompt=system_prompt(self.framework), user_content=user_content(action, workflow_context),
            response_model=EthicalJudgment, request_id=request_id, max_output_tokens=self.max_output_tokens,
            temperature=self.temperature, seed=self.seed,
            tags={"monitor_id": self.monitor_id, "framework": self.framework.value, "action_id": action.action_id})
        call = result.model_dump(mode="json", exclude={"response"})
        if not result.success or result.response is None:
            kind = result.error.kind.value if result.error else "PROVIDER_ERROR"
            raise ProviderCallError(kind, result.error.message if result.error else "no response", result)
        try:
            judgment = EthicalJudgment.model_validate(result.response)
        except ValidationError as exc:
            raise ProviderCallError("INVALID_RESPONSE", str(exc)[:300], result) from None
        citable = set(citable_event_ids(workflow_context))
        invented = [e for e in judgment.evidence_event_ids if e not in citable]
        if invented:
            raise ProviderCallError("INVALID_EVIDENCE", f"cited {len(invented)} event ID(s) not in the supplied context",
                                    result, details={"invalid_evidence_ids": invented[:50]})
        explanation = judgment.explanation.strip()
        if not explanation:
            raise ProviderCallError("INVALID_RESPONSE", "empty explanation", result)
        if self._provider.is_synthetic:
            explanation = f"{SYNTHETIC_PREFIX} {explanation}"
        return EthicalAssessment(
            framework=self.framework, action_id=action.action_id, workflow_id=action.workflow_id,
            risk_level=judgment.risk_level, concerns=[c[:300] for c in judgment.concerns[:20]],
            evidence_event_ids=list(dict.fromkeys(judgment.evidence_event_ids))[:50],
            uncertainty=judgment.uncertainty, recommendation=judgment.recommendation, explanation=explanation[:4000],
            monitor_id=self.monitor_id, is_mock=self._provider.is_synthetic, provider=self._provider.name,
            model=self._provider.model, prompt_version=PROMPT_VERSION, prompt_hash=prompt_hash(self.framework),
            legitimate_interpretations=[x[:300] for x in judgment.legitimate_interpretations[:10]],
            insufficient_evidence=judgment.insufficient_evidence, call=call,
        )
