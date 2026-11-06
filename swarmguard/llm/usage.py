"""Cost, request and concurrency controls, plus a provider-agnostic retry wrapper.

Stack used by the factory (outermost first)::

    RetryingProvider  -> GuardedProvider -> concrete provider (mock or OpenAI)

so every *attempt*, including retries, is counted and budgeted.

Budget accounting (``GuardedProvider``):

* **Before** each attempt, a conservative maximum cost is *reserved*:
  ``(utf8_bytes(system + user) + overhead) * input_price + max_output_tokens * output_price``.
  A BPE token covers at least one byte, so the byte count bounds the input tokens. The
  output is capped by ``max_output_tokens``, which is sent to the provider.
* If ``spent + reserved + new reservation`` would exceed the budget, the attempt is refused
  with ``BUDGET_EXCEEDED`` and no request is made.
* After the attempt, the reservation is settled: with measured usage if the provider
  reported it, otherwise the full reservation is kept as spent.
* ``max_requests`` caps attempts regardless of pricing.

This is local accounting. It is NOT a substitute for provider-side billing limits.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from swarmguard.llm.base import LLMProvider
from swarmguard.llm.schemas import LLMError, LLMErrorKind, ProviderResult

PROMPT_OVERHEAD_TOKENS = 64


@dataclass(frozen=True)
class BudgetPolicy:
    max_requests: int | None = None
    max_cost_usd: float | None = None
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None

    @property
    def priced(self) -> bool:
        return self.price_input_per_mtok is not None and self.price_output_per_mtok is not None

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if not self.priced:
            return None
        assert self.price_input_per_mtok is not None and self.price_output_per_mtok is not None
        return (input_tokens * self.price_input_per_mtok + output_tokens * self.price_output_per_mtok) / 1_000_000


def max_input_tokens(system_prompt: str, user_content: str) -> int:
    return len(system_prompt.encode("utf-8")) + len(user_content.encode("utf-8")) + PROMPT_OVERHEAD_TOKENS


@dataclass
class UsageLedger:
    attempts: int = 0
    refused: int = 0
    succeeded: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_unavailable: int = 0
    reserved_usd: float = 0.0
    spent_usd: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {"attempts": self.attempts, "refused": self.refused, "succeeded": self.succeeded,
                "failed": self.failed, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "calls_without_usage": self.usage_unavailable, "reserved_usd_outstanding": round(self.reserved_usd, 6),
                "spent_usd_conservative": round(self.spent_usd, 6), "errors": dict(sorted(self.errors.items()))}


class GuardedProvider:
    """Wraps a provider with request caps, conservative budget reservation and bounded concurrency."""

    def __init__(self, inner: LLMProvider, policy: BudgetPolicy, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._inner = inner
        self.policy = policy
        self.ledger = UsageLedger()
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max_concurrency)
        self.name, self.model, self.is_synthetic = inner.name, inner.model, inner.is_synthetic
        self.max_concurrency = max_concurrency
        self.exhausted = False
        self._in_flight = 0
        self.max_in_flight_observed = 0

    def _refuse(self, kind: LLMErrorKind, message: str, request_id: str) -> ProviderResult:
        self.ledger.refused += 1
        self.ledger.errors[kind.value] = self.ledger.errors.get(kind.value, 0) + 1
        self.exhausted = True
        return ProviderResult(provider=self.name, model=self.model, request_id=request_id, success=False,
                              error=LLMError.of(kind, message), attempts=0, is_synthetic=self.is_synthetic)

    async def generate_structured(self, *, system_prompt: str, user_content: str, response_model: type[BaseModel],
                                  request_id: str, max_output_tokens: int, temperature: float | None = None,
                                  seed: int | None = None, tags: dict[str, str] | None = None) -> ProviderResult:
        reservation = self.policy.cost(max_input_tokens(system_prompt, user_content), max_output_tokens)
        async with self._lock:
            if self.policy.max_requests is not None and self.ledger.attempts >= self.policy.max_requests:
                return self._refuse(LLMErrorKind.REQUEST_LIMIT,
                                    f"request limit {self.policy.max_requests} reached", request_id)
            if self.policy.max_cost_usd is not None and reservation is not None and (
                    self.ledger.spent_usd + self.ledger.reserved_usd + reservation > self.policy.max_cost_usd):
                return self._refuse(LLMErrorKind.BUDGET_EXCEEDED,
                                    f"reserving ${reservation:.4f} would exceed the ${self.policy.max_cost_usd:.2f} "
                                    f"budget (spent ${self.ledger.spent_usd:.4f}, reserved "
                                    f"${self.ledger.reserved_usd:.4f})", request_id)
            self.ledger.attempts += 1
            if reservation is not None:
                self.ledger.reserved_usd += reservation
        async with self._sem:
            self._in_flight += 1
            self.max_in_flight_observed = max(self.max_in_flight_observed, self._in_flight)
            try:
                result = await self._inner.generate_structured(
                    system_prompt=system_prompt, user_content=user_content, response_model=response_model,
                    request_id=request_id, max_output_tokens=max_output_tokens, temperature=temperature, seed=seed,
                    tags=tags)
            except Exception as exc:  # noqa: BLE001 - a provider must not raise, but never let one escape
                result = ProviderResult(provider=self.name, model=self.model, request_id=request_id, success=False,
                                        error=LLMError.of(LLMErrorKind.PROVIDER_ERROR,
                                                          f"{type(exc).__name__}: {exc}"),
                                        is_synthetic=self.is_synthetic)
            finally:
                self._in_flight -= 1
        async with self._lock:
            actual = None
            if result.usage.available and result.usage.input_tokens is not None and result.usage.output_tokens is not None:
                self.ledger.input_tokens += result.usage.input_tokens
                self.ledger.output_tokens += result.usage.output_tokens
                actual = self.policy.cost(result.usage.input_tokens, result.usage.output_tokens)
            else:
                self.ledger.usage_unavailable += 1
            if reservation is not None:
                self.ledger.reserved_usd -= reservation
                self.ledger.spent_usd += actual if actual is not None else reservation
            if result.success:
                self.ledger.succeeded += 1
            else:
                self.ledger.failed += 1
                kind = result.error.kind.value if result.error else "UNKNOWN"
                self.ledger.errors[kind] = self.ledger.errors.get(kind, 0) + 1
        return result.model_copy(update={"reserved_cost_usd": reservation, "estimated_cost_usd": actual})


class RetryingProvider:
    """Retries only retryable failures (rate limit, timeout, network), at most ``max_retries`` times."""

    def __init__(self, inner: LLMProvider, *, max_retries: int = 2, base_delay_s: float = 0.5,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._inner = inner
        self.max_retries = max_retries
        self._delay = base_delay_s
        self._sleep = sleep
        self.name, self.model, self.is_synthetic = inner.name, inner.model, inner.is_synthetic

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    async def generate_structured(self, **kwargs: Any) -> ProviderResult:
        attempts = 0
        total_ms = 0.0
        while True:
            attempts += 1
            result = await self._inner.generate_structured(**kwargs)
            total_ms += result.latency_ms or 0.0
            retry = (not result.success and result.error is not None and result.error.retryable
                     and attempts <= self.max_retries)
            if not retry:
                return result.model_copy(update={"attempts": attempts,
                                                  "latency_ms": total_ms if result.latency_ms is not None else None})
            await self._sleep(self._delay * (2 ** (attempts - 1)))


def find_guard(provider: Any) -> GuardedProvider | None:
    """Locate the GuardedProvider in a wrapper stack (for ledger reporting)."""
    seen = 0
    while provider is not None and seen < 10:
        if isinstance(provider, GuardedProvider):
            return provider
        provider = getattr(provider, "inner", None) or getattr(provider, "_inner", None)
        seen += 1
    return None
