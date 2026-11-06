"""Deterministic mock provider: no network, no API key, reproducible, with failure injection.

Every result has ``is_synthetic=True``. Mock output is NOT model reasoning and must never be
reported as evidence of LLM monitor performance.

Each request takes its behavior from a ``MockScript``:

* ``VALID``: return ``response`` (or the ``responder``'s output), validated against the response model
* ``INVALID_JSON`` / ``MISSING_FIELDS``: malformed output, rejected as ``INVALID_RESPONSE``
* ``TIMEOUT`` / ``RATE_LIMIT`` / ``NETWORK`` / ``AUTHENTICATION`` / ``PROVIDER_ERROR``: structured failures
* ``HANG``: sleep ``hang_seconds`` (exercises caller-side timeouts)
* ``RAISE``: raise an exception (exercises wrappers that must contain it)

Behaviors can be overridden per tag value (e.g. ``monitor_id`` or ``framework``) and queued
per key for retry tests. Usage is reported as unavailable unless ``usage`` is scripted.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from swarmguard.llm.schemas import LLMError, LLMErrorKind, LLMUsage, ProviderResult


class MockBehavior(StrEnum):
    VALID = "VALID"
    INVALID_JSON = "INVALID_JSON"
    MISSING_FIELDS = "MISSING_FIELDS"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK = "NETWORK"
    AUTHENTICATION = "AUTHENTICATION"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    HANG = "HANG"
    RAISE = "RAISE"


_FAILURES = {
    MockBehavior.TIMEOUT: LLMErrorKind.TIMEOUT, MockBehavior.RATE_LIMIT: LLMErrorKind.RATE_LIMIT,
    MockBehavior.NETWORK: LLMErrorKind.NETWORK, MockBehavior.AUTHENTICATION: LLMErrorKind.AUTHENTICATION,
    MockBehavior.PROVIDER_ERROR: LLMErrorKind.PROVIDER_ERROR,
}

Responder = Callable[[str, str, dict[str, str]], dict[str, Any]]


@dataclass
class MockStep:
    behavior: MockBehavior = MockBehavior.VALID
    response: dict[str, Any] | None = None
    usage: tuple[int, int] | None = None


@dataclass
class MockScript:
    default: MockStep = field(default_factory=MockStep)
    by_tag: dict[str, MockStep] = field(default_factory=dict)
    queues: dict[str, list[MockStep]] = field(default_factory=dict)

    def step_for(self, tags: dict[str, str]) -> MockStep:
        for value in tags.values():
            queue = self.queues.get(value)
            if queue:
                return queue.pop(0)
        for value in tags.values():
            if value in self.by_tag:
                return self.by_tag[value]
        return self.default


class MockLLMProvider:
    name = "mock"
    is_synthetic = True

    def __init__(self, script: MockScript | None = None, *, responder: Responder | None = None,
                 model: str = "mock-deterministic-v1", hang_seconds: float = 5.0,
                 timer: Callable[[], float] = time.perf_counter) -> None:
        self.script = script or MockScript()
        self.responder = responder
        self.model = model
        self._hang = hang_seconds
        self._timer = timer
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(self, *, system_prompt: str, user_content: str, response_model: type[BaseModel],
                                  request_id: str, max_output_tokens: int, temperature: float | None = None,
                                  seed: int | None = None, tags: dict[str, str] | None = None) -> ProviderResult:
        tags = dict(tags or {})
        start = self._timer()
        self.calls.append({"request_id": request_id, "tags": tags, "system_prompt": system_prompt,
                           "user_content": user_content, "max_output_tokens": max_output_tokens,
                           "temperature": temperature, "seed": seed})
        step = self.script.step_for(tags)
        applied = {"max_output_tokens": max_output_tokens, **({"temperature": temperature} if temperature is not None
                                                              else {}), **({"seed": seed} if seed is not None else {})}

        def done(**kw: Any) -> ProviderResult:
            usage = LLMUsage(available=True, input_tokens=step.usage[0], output_tokens=step.usage[1],
                             total_tokens=sum(step.usage)) if step.usage else LLMUsage.unavailable()
            return ProviderResult(provider=self.name, model=self.model, request_id=request_id, is_synthetic=True,
                                  usage=usage, latency_ms=(self._timer() - start) * 1000, settings_applied=applied,
                                  provider_request_id=f"mock-{request_id}", **kw)

        if step.behavior is MockBehavior.RAISE:
            raise RuntimeError("injected mock provider exception")
        if step.behavior is MockBehavior.HANG:
            await asyncio.sleep(self._hang)
        if step.behavior in _FAILURES:
            return done(success=False, error=LLMError.of(_FAILURES[step.behavior],
                                                         f"injected {step.behavior.value.lower()}"))
        if step.behavior is MockBehavior.INVALID_JSON:
            raw: Any = "{not valid json"
        elif step.response is not None:
            raw = dict(step.response)
        elif self.responder is not None:
            raw = self.responder(system_prompt, user_content, tags)
        else:
            raw = {}
        if step.behavior is MockBehavior.MISSING_FIELDS and isinstance(raw, dict):
            raw = {k: v for k, v in raw.items() if k not in ("recommendation", "risk_level")}
        try:
            parsed = (response_model.model_validate_json(raw) if isinstance(raw, str)
                      else response_model.model_validate(raw))
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            return done(success=False, error=LLMError.of(LLMErrorKind.INVALID_RESPONSE,
                                                         f"structured output failed validation: {exc}"[:500]))
        return done(success=True, response=parsed.model_dump(mode="json"))
