"""OpenAI provider using the official SDK's Responses API structured outputs (verified against openai 3.46).

* ``client.responses.with_raw_response.parse(model=..., instructions=<trusted system prompt>,
  input=<untrusted JSON data>, text_format=<Pydantic model>, max_output_tokens=..., store=False, timeout=...)``.
  The raw form keeps the HTTP body, so measured token usage survives a structured-output validation failure.
* The SDK's own retries are disabled (``max_retries=0``). Retries happen in ``RetryingProvider``, so
  every attempt passes through the budget guard.
* Errors become ``ProviderResult(success=False, error=LLMError(kind=...))``. Nothing is ever replaced
  with a mock answer.
* The API key never appears in logs, reprs, results or error messages (messages are scrubbed).
* The provider only returns validated data. It holds no SwarmGuard capability.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from swarmguard.llm.configuration import LLMConfigurationError, LLMSettings
from swarmguard.llm.schemas import LLMError, LLMErrorKind, LLMUsage, ProviderResult

logger = logging.getLogger("swarmguard.llm.openai")
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


class OpenAIProvider:
    name = "openai"
    is_synthetic = False

    def __init__(self, settings: LLMSettings, *, client: Any | None = None,
                 timer: Callable[[], float] = time.perf_counter) -> None:
        if settings.provider != "openai":
            raise LLMConfigurationError("OpenAIProvider requires provider=openai settings")
        settings.validate()
        assert settings.api_key is not None and settings.model is not None
        self.model: str = settings.model
        self._secret = settings.api_key
        self._timeout = settings.timeout_seconds
        self._timer = timer
        self._client = client or openai.AsyncOpenAI(api_key=settings.api_key.get_secret_value(),
                                                    timeout=settings.timeout_seconds, max_retries=0)

    def __repr__(self) -> str:
        return f"OpenAIProvider(model={self.model!r}, api_key=<redacted>)"

    def _scrub(self, text: str) -> str:
        secret = self._secret.get_secret_value()
        text = text.replace(secret, "<redacted>") if secret else text
        return _KEY_PATTERN.sub("sk-<redacted>", text)[:1000]

    def _classify(self, exc: BaseException) -> LLMError:
        status = getattr(exc, "status_code", None)
        message = self._scrub(f"{type(exc).__name__}: {exc}")
        lowered = message.lower()
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            kind = LLMErrorKind.AUTHENTICATION
        elif isinstance(exc, openai.RateLimitError):
            kind = LLMErrorKind.RATE_LIMIT
        elif isinstance(exc, (openai.APITimeoutError, asyncio.TimeoutError, TimeoutError)):
            kind = LLMErrorKind.TIMEOUT
        elif isinstance(exc, openai.APIConnectionError):
            kind = LLMErrorKind.NETWORK
        elif isinstance(exc, openai.LengthFinishReasonError):
            kind = LLMErrorKind.INVALID_RESPONSE  # output truncated at max_output_tokens
        elif isinstance(exc, (ValidationError, openai.ContentFilterFinishReasonError, openai.APIResponseValidationError)):
            kind = LLMErrorKind.INVALID_RESPONSE
        elif isinstance(exc, openai.BadRequestError) and ("context_length" in lowered or "maximum context" in lowered):
            kind = LLMErrorKind.CONTEXT_LENGTH
        elif isinstance(exc, (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError)):
            kind = LLMErrorKind.UNSUPPORTED_CONFIGURATION
        else:
            kind = LLMErrorKind.PROVIDER_ERROR
        return LLMError.of(kind, message, status_code=status)

    async def generate_structured(self, *, system_prompt: str, user_content: str, response_model: type[BaseModel],
                                  request_id: str, max_output_tokens: int, temperature: float | None = None,
                                  seed: int | None = None, tags: dict[str, str] | None = None) -> ProviderResult:
        kwargs: dict[str, Any] = {"model": self.model, "instructions": system_prompt, "input": user_content,
                                  "text_format": response_model, "max_output_tokens": max_output_tokens,
                                  "store": False, "timeout": self._timeout}
        unsupported: list[str] = []
        if temperature is not None:
            kwargs["temperature"] = temperature
        if seed is not None:
            unsupported.append("seed (not a Responses API parameter; omitted)")
        applied = {k: kwargs[k] for k in ("max_output_tokens", "store", "timeout") if k in kwargs}
        if "temperature" in kwargs:
            applied["temperature"] = temperature
        start = self._timer()

        def result(**kw: Any) -> ProviderResult:
            return ProviderResult(provider=self.name, model=self.model, request_id=request_id, is_synthetic=False,
                                  latency_ms=(self._timer() - start) * 1000, settings_applied=applied,
                                  settings_unsupported=unsupported, **kw)

        try:
            # with_raw_response keeps the HTTP body, so billed usage is recorded even when parsing fails.
            raw = await asyncio.wait_for(self._client.responses.with_raw_response.parse(**kwargs),
                                         timeout=self._timeout + 5)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a recorded result
            error = self._classify(exc)
            logger.warning("openai request %s failed: %s", request_id, error.kind.value)
            return result(success=False, error=error, provider_request_id=getattr(exc, "request_id", None))
        provider_id = raw.headers.get("x-request-id")
        usage = LLMUsage.unavailable()
        try:
            u = (raw.http_response.json() or {}).get("usage") or {}
            if u.get("input_tokens") is not None and u.get("output_tokens") is not None:
                usage = LLMUsage(available=True, input_tokens=int(u["input_tokens"]),
                                 output_tokens=int(u["output_tokens"]), total_tokens=u.get("total_tokens"))
        except (ValueError, TypeError, AttributeError):
            pass
        try:
            response = raw.parse()
        except Exception as exc:  # noqa: BLE001 - malformed structured output
            error = self._classify(exc)
            logger.warning("openai request %s returned unusable output: %s", request_id, error.kind.value)
            return result(success=False, error=error, usage=usage, provider_request_id=provider_id)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            status = getattr(response, "status", None)
            return result(success=False, usage=usage, provider_request_id=provider_id,
                          error=LLMError.of(LLMErrorKind.INVALID_RESPONSE,
                                            f"no structured output (status={status}; refusal or incomplete)"))
        try:
            validated = response_model.model_validate(parsed.model_dump() if isinstance(parsed, BaseModel) else parsed)
        except ValidationError as exc:
            return result(success=False, usage=usage, provider_request_id=provider_id,
                          error=self._classify(exc))
        return result(success=True, response=validated.model_dump(mode="json"), usage=usage,
                      provider_request_id=provider_id)
