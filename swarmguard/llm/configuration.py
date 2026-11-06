"""LLM settings from environment variables. The API key is held as a secret and never logged.

Variables (see ``.env.example``):

    SWARMGUARD_LLM_PROVIDER            mock | openai           (default mock)
    OPENAI_API_KEY                     required for openai
    SWARMGUARD_OPENAI_MODEL            required for openai (no silent default model)
    SWARMGUARD_LLM_TIMEOUT_SECONDS     default 30
    SWARMGUARD_LLM_MAX_RETRIES         default 2
    SWARMGUARD_LLM_MAX_CONCURRENCY     default 4
    SWARMGUARD_LLM_MAX_REQUESTS        hard cap on provider attempts per process (unset = no cap)
    SWARMGUARD_LLM_MAX_OUTPUT_TOKENS   default 800 (same for every monitor)
    SWARMGUARD_LLM_TEMPERATURE         unset = provider default (recorded as such)
    SWARMGUARD_LLM_SEED                optional; recorded as unsupported if the provider ignores it
    SWARMGUARD_MAX_EVALUATION_COST_USD default 5.00
    SWARMGUARD_PRICE_INPUT_USD_PER_MTOK / SWARMGUARD_PRICE_OUTPUT_USD_PER_MTOK
                                       pricing used for conservative cost reservation (no built-in prices)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import SecretStr

PROVIDERS = ("mock", "openai")


class LLMConfigurationError(ValueError):
    """Missing or unsafe live configuration. Raised before any request is made."""


def _float(env: Mapping[str, str], key: str, default: float | None) -> float | None:
    raw = env.get(key, "").strip()
    return float(raw) if raw else default


def _int(env: Mapping[str, str], key: str, default: int | None) -> int | None:
    raw = env.get(key, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "mock"
    model: str | None = None
    api_key: SecretStr | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    max_retries: int = 2
    max_concurrency: int = 4
    max_requests: int | None = None
    max_output_tokens: int = 800
    temperature: float | None = None
    seed: int | None = None
    max_cost_usd: float | None = 5.0
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, provider: str | None = None) -> LLMSettings:
        env = os.environ if env is None else env
        key = env.get("OPENAI_API_KEY", "").strip()
        return cls(
            provider=(provider or env.get("SWARMGUARD_LLM_PROVIDER", "mock")).strip().lower() or "mock",
            model=env.get("SWARMGUARD_OPENAI_MODEL", "").strip() or None,
            api_key=SecretStr(key) if key else None,
            timeout_seconds=_float(env, "SWARMGUARD_LLM_TIMEOUT_SECONDS", 30.0) or 30.0,
            max_retries=_int(env, "SWARMGUARD_LLM_MAX_RETRIES", 2) or 0,
            max_concurrency=_int(env, "SWARMGUARD_LLM_MAX_CONCURRENCY", 4) or 1,
            max_requests=_int(env, "SWARMGUARD_LLM_MAX_REQUESTS", None),
            max_output_tokens=_int(env, "SWARMGUARD_LLM_MAX_OUTPUT_TOKENS", 800) or 800,
            temperature=_float(env, "SWARMGUARD_LLM_TEMPERATURE", None),
            seed=_int(env, "SWARMGUARD_LLM_SEED", None),
            max_cost_usd=_float(env, "SWARMGUARD_MAX_EVALUATION_COST_USD", 5.0),
            price_input_per_mtok=_float(env, "SWARMGUARD_PRICE_INPUT_USD_PER_MTOK", None),
            price_output_per_mtok=_float(env, "SWARMGUARD_PRICE_OUTPUT_USD_PER_MTOK", None),
        )

    @property
    def has_pricing(self) -> bool:
        return self.price_input_per_mtok is not None and self.price_output_per_mtok is not None

    def validate(self) -> LLMSettings:
        """Fail closed on any unsafe or incomplete configuration. Mock mode needs nothing."""
        if self.provider not in PROVIDERS:
            raise LLMConfigurationError(f"unknown provider '{self.provider}' (expected one of {PROVIDERS})")
        if self.timeout_seconds <= 0 or self.max_retries < 0 or self.max_concurrency < 1 or self.max_output_tokens < 1:
            raise LLMConfigurationError("timeout, retries, concurrency and max_output_tokens must be positive")
        if self.max_retries > 5:
            raise LLMConfigurationError("SWARMGUARD_LLM_MAX_RETRIES above 5 is not allowed")
        if self.provider == "openai":
            if self.api_key is None or not self.api_key.get_secret_value():
                raise LLMConfigurationError("live mode requires OPENAI_API_KEY in the environment")
            if not self.model:
                raise LLMConfigurationError("live mode requires SWARMGUARD_OPENAI_MODEL (no default model is chosen)")
            if self.max_cost_usd is not None and not self.has_pricing and self.max_requests is None:
                raise LLMConfigurationError(
                    "a dollar budget needs SWARMGUARD_PRICE_INPUT_USD_PER_MTOK and SWARMGUARD_PRICE_OUTPUT_USD_PER_MTOK "
                    "for conservative reservation; otherwise set SWARMGUARD_LLM_MAX_REQUESTS explicitly")
            if self.max_cost_usd is None and self.max_requests is None:
                raise LLMConfigurationError("live mode requires a cost budget or SWARMGUARD_LLM_MAX_REQUESTS")
        return self

    def public_dict(self) -> dict[str, object]:
        """Safe to persist: no credential, only whether one is configured."""
        return {
            "provider": self.provider, "model": self.model, "api_key_configured": self.api_key is not None,
            "timeout_seconds": self.timeout_seconds, "max_retries": self.max_retries,
            "max_concurrency": self.max_concurrency, "max_requests": self.max_requests,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature if self.temperature is not None else "provider default",
            "seed": self.seed, "max_cost_usd": self.max_cost_usd,
            "price_input_usd_per_mtok": self.price_input_per_mtok,
            "price_output_usd_per_mtok": self.price_output_per_mtok,
        }


def load_env_file(path: str = ".env") -> bool:
    """Optionally load a local .env (python-dotenv). Existing environment variables win."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dependency
        return False
    return bool(load_dotenv(path, override=False))
