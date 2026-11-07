"""Provider injection for the console's RQ1 laboratory (entry-point side; the only place with network access).

Live calls are possible only when ALL of these hold, checked before any request:

* the operator started the server with ``SWARMGUARD_CONSOLE_LIVE_LLM=1`` (a browser can never switch it on);
* ``OPENAI_API_KEY`` and ``SWARMGUARD_OPENAI_MODEL`` are set;
* positive per-million-token prices are set (``SWARMGUARD_PRICE_INPUT_USD_PER_MTOK`` / ``..._OUTPUT_...``), so every
  request is reserved against a dollar budget;
* each experiment carries its own request cap and dollar cap plus an explicit paid-call confirmation. The effective
  caps are the minimum of the experiment's and the server's (``SWARMGUARD_LLM_MAX_REQUESTS``,
  ``SWARMGUARD_MAX_LLM_REQUESTS``, ``SWARMGUARD_MAX_EVALUATION_COST_USD``).

Values come from the process environment, falling back to ``.env`` (the environment wins). The API key is never
returned, logged or persisted.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
from collections.abc import Mapping
from typing import Any

from swarmguard.ethics.synthetic import rules_responder
from swarmguard.llm.configuration import LLMConfigurationError, LLMSettings
from swarmguard.research.rq1_llm import LLMRuntime
from swarmguard_providers.factory import build_provider

LIVE_SWITCH = "SWARMGUARD_CONSOLE_LIVE_LLM"


def _env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    merged: dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        merged.update({k: v for k, v in dotenv_values(".env").items() if v is not None})
    except ImportError:  # pragma: no cover - optional dependency
        pass
    merged.update(os.environ)
    return merged


def _int(env: Mapping[str, str], key: str) -> int | None:
    raw = env.get(key, "").strip()
    return int(raw) if raw else None


def _caps(env: Mapping[str, str]) -> tuple[int | None, float | None]:
    requests = [v for v in (_int(env, "SWARMGUARD_LLM_MAX_REQUESTS"), _int(env, "SWARMGUARD_MAX_LLM_REQUESTS")) if v]
    cost_raw = env.get("SWARMGUARD_MAX_EVALUATION_COST_USD", "").strip()
    return (min(requests) if requests else None), (float(cost_raw) if cost_raw else None)


def make_rq1_llm(env: Mapping[str, str] | None = None, *, client: Any | None = None):
    """Returns ``(factory, status)`` for ``create_console_app``. ``client`` lets tests inject a fake OpenAI client."""
    values = _env(env)
    req_cap, cost_cap = _caps(values)

    def status() -> dict[str, Any]:
        live_reasons: list[str] = []
        settings = LLMSettings.from_env(values, provider="openai")
        if values.get(LIVE_SWITCH, "").strip() != "1":
            live_reasons.append(f"the server was not started with {LIVE_SWITCH}=1 (operator opt-in for paid calls)")
        if client is None and importlib.util.find_spec("openai") is None:
            live_reasons.append("the openai package is not installed in this environment")
        if not settings.api_key:
            live_reasons.append("OPENAI_API_KEY is not set")
        if not settings.model:
            live_reasons.append("SWARMGUARD_OPENAI_MODEL is not set")
        if not settings.has_pricing or not (settings.price_input_per_mtok or 0) > 0 or not (settings.price_output_per_mtok or 0) > 0:
            live_reasons.append("positive SWARMGUARD_PRICE_INPUT_USD_PER_MTOK and SWARMGUARD_PRICE_OUTPUT_USD_PER_MTOK are required")
        common = {"max_output_tokens": settings.max_output_tokens, "max_requests_cap": req_cap, "max_cost_cap_usd": cost_cap}
        return {
            "mock": {"available": True, "reasons": [], "provider": "mock", "model": "mock-deterministic-v1", **common,
                     "price_input_usd_per_mtok": 0.0, "price_output_usd_per_mtok": 0.0},
            "live": {"available": not live_reasons, "reasons": live_reasons, "provider": "openai", "model": settings.model,
                     "price_input_usd_per_mtok": settings.price_input_per_mtok,
                     "price_output_usd_per_mtok": settings.price_output_per_mtok, **common},
        }

    def factory(mode: str, max_requests: int, max_cost_usd: float | None) -> LLMRuntime:
        requests = min(x for x in (max_requests, req_cap) if x)
        if mode == "live":
            problems = status()["live"]["reasons"]
            if problems:
                raise LLMConfigurationError("live LLM mode unavailable: " + "; ".join(problems))
            if max_cost_usd is None or max_cost_usd <= 0:
                raise LLMConfigurationError("live LLM mode requires a positive cost cap")
            cost = min(x for x in (max_cost_usd, cost_cap) if x)
            settings = dataclasses.replace(LLMSettings.from_env(values, provider="openai"), max_requests=requests,
                                           max_cost_usd=cost)
        elif mode == "mock":
            settings = dataclasses.replace(LLMSettings.from_env(values, provider="mock"), max_requests=requests,
                                           max_cost_usd=None, price_input_per_mtok=None, price_output_per_mtok=None)
        else:
            raise LLMConfigurationError(f"unknown LLM mode {mode!r}")
        provider, guard = build_provider(settings, mock_responder=rules_responder, client=client)
        return LLMRuntime(provider=provider, guard=guard, settings=settings.public_dict(),
                          max_output_tokens=settings.max_output_tokens, temperature=settings.temperature, seed=settings.seed)

    return factory, status
