"""Builds the provider stack used by entry-point scripts: Retrying -> Guarded -> (Mock | OpenAI)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from swarmguard.llm.configuration import LLMSettings
from swarmguard.llm.mock_provider import MockLLMProvider, MockScript, Responder
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider, RetryingProvider


def build_provider(settings: LLMSettings, *, mock_script: MockScript | None = None,
                   mock_responder: Responder | None = None, client: Any | None = None,
                   sleep: Callable[[float], Awaitable[None]] | None = None) -> tuple[RetryingProvider, GuardedProvider]:
    """Returns (provider to use, guard holding the usage ledger). Validates settings first (fail closed)."""
    settings.validate()
    if settings.provider == "openai":
        from swarmguard_providers.openai_provider import OpenAIProvider

        inner: Any = OpenAIProvider(settings, client=client)
    else:
        inner = MockLLMProvider(mock_script, responder=mock_responder)
    policy = BudgetPolicy(max_requests=settings.max_requests,
                          max_cost_usd=settings.max_cost_usd if settings.has_pricing else None,
                          price_input_per_mtok=settings.price_input_per_mtok,
                          price_output_per_mtok=settings.price_output_per_mtok)
    guard = GuardedProvider(inner, policy, max_concurrency=settings.max_concurrency)
    kwargs: dict[str, Any] = {"max_retries": settings.max_retries}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return RetryingProvider(guard, **kwargs), guard
