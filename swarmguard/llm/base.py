"""The provider interface. Providers only return structured data; they hold no SwarmGuard capability."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from swarmguard.llm.schemas import ProviderResult

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    """Asynchronous structured generation.

    Implementations must NOT raise for provider-side failures: they return
    ``ProviderResult(success=False, error=...)`` so failures are recorded, never hidden.
    A successful result's ``response`` has already been validated against ``response_model``.
    Optional capabilities (temperature, seed) that a provider cannot honor are omitted and
    listed in ``settings_unsupported``.
    """

    name: str
    model: str
    is_synthetic: bool

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        request_id: str,
        max_output_tokens: int,
        temperature: float | None = None,
        seed: int | None = None,
        tags: dict[str, str] | None = None,
    ) -> ProviderResult: ...


class ProviderCallError(RuntimeError):
    """Raised by *monitors* (not providers) when a call failed or its output was unusable."""

    def __init__(self, kind: str, message: str, result: ProviderResult | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.result = result
        self.details = details or {}
