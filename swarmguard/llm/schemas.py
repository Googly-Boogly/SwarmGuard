"""Structured provider results. Usage and timing are measured or marked unavailable, never invented."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMErrorKind(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNSUPPORTED_CONFIGURATION = "UNSUPPORTED_CONFIGURATION"
    CONTEXT_LENGTH = "CONTEXT_LENGTH"
    MISSING_CONFIGURATION = "MISSING_CONFIGURATION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    REQUEST_LIMIT = "REQUEST_LIMIT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


RETRYABLE = frozenset({LLMErrorKind.RATE_LIMIT, LLMErrorKind.TIMEOUT, LLMErrorKind.NETWORK})


class LLMError(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: LLMErrorKind
    message: str = Field(max_length=1000)
    retryable: bool = False
    status_code: int | None = None

    @classmethod
    def of(cls, kind: LLMErrorKind, message: str, status_code: int | None = None) -> LLMError:
        return cls(kind=kind, message=message[:1000], retryable=kind in RETRYABLE, status_code=status_code)


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def unavailable(cls) -> LLMUsage:
        return cls()


class ProviderResult(BaseModel):
    """One logical structured-generation call (possibly several attempts)."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    request_id: str = Field(description="SwarmGuard request ID (deterministic per run).")
    provider_request_id: str | None = Field(default=None, description="ID returned by the provider, if any.")
    success: bool
    response: dict[str, Any] | None = None
    error: LLMError | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage.unavailable)
    latency_ms: float | None = Field(default=None, description="Measured wall time across all attempts.")
    attempts: int = 1
    is_synthetic: bool = Field(description="True for mock providers: not model reasoning.")
    settings_applied: dict[str, Any] = Field(default_factory=dict)
    settings_unsupported: list[str] = Field(default_factory=list)
    reserved_cost_usd: float | None = None
    estimated_cost_usd: float | None = Field(default=None, description="From measured usage x configured pricing.")
