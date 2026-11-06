"""LLM provider infrastructure (spec items 1-10), plus budget and concurrency controls."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from swarmguard.ethics.prompts import EthicalJudgment
from swarmguard.llm.configuration import LLMConfigurationError, LLMSettings
from swarmguard.llm.mock_provider import MockBehavior, MockLLMProvider, MockScript, MockStep
from swarmguard.llm.schemas import LLMErrorKind
from swarmguard.llm.usage import BudgetPolicy, GuardedProvider, RetryingProvider
from swarmguard_providers.factory import build_provider
from swarmguard_providers.openai_provider import OpenAIProvider
from tests.conftest import run
from tests.stage3_helpers import FAKE_KEY, error_handler, fake_openai, judgment, ok_handler, openai_settings

KW = dict(system_prompt="sys", user_content='{"x":1}', response_model=EthicalJudgment, request_id="r1",
          max_output_tokens=100)


async def _no_sleep(_s):
    return None


def _mock(step: MockStep) -> MockLLMProvider:
    return MockLLMProvider(MockScript(default=step))


def test_1_mock_returns_valid_structured_output():
    r = run(_mock(MockStep(response=judgment())).generate_structured(**KW))
    assert r.success and r.is_synthetic and EthicalJudgment.model_validate(r.response).recommendation.value == "ESCALATE"
    assert r.usage.available is False  # mock never invents token usage


@pytest.mark.parametrize("behavior", [MockBehavior.INVALID_JSON, MockBehavior.MISSING_FIELDS])
def test_2_3_malformed_or_incomplete_output_rejected(behavior):
    r = run(_mock(MockStep(behavior, response=judgment())).generate_structured(**KW))
    assert not r.success and r.error.kind is LLMErrorKind.INVALID_RESPONSE and r.response is None
    bad = run(_mock(MockStep(response={**judgment(), "recommendation": "MAYBE"})).generate_structured(**KW))
    assert bad.error.kind is LLMErrorKind.INVALID_RESPONSE


def test_4_5_timeouts_and_exceptions_are_recorded():
    t = run(_mock(MockStep(MockBehavior.TIMEOUT)).generate_structured(**KW))
    assert t.error.kind is LLMErrorKind.TIMEOUT and t.error.retryable
    guarded = GuardedProvider(_mock(MockStep(MockBehavior.RAISE)), BudgetPolicy())
    e = run(guarded.generate_structured(**KW))  # a raising provider is contained, never propagated
    assert not e.success and e.error.kind is LLMErrorKind.PROVIDER_ERROR and guarded.ledger.failed == 1


def test_6_8_live_mode_requires_explicit_configuration():
    assert LLMSettings.from_env({}).provider == "mock" and LLMSettings.from_env({}).validate()
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        LLMSettings.from_env({"SWARMGUARD_LLM_PROVIDER": "openai"}).validate()
    with pytest.raises(LLMConfigurationError, match="SWARMGUARD_OPENAI_MODEL"):
        LLMSettings.from_env({"SWARMGUARD_LLM_PROVIDER": "openai", "OPENAI_API_KEY": FAKE_KEY,
                              "SWARMGUARD_LLM_MAX_REQUESTS": "5"}).validate()
    with pytest.raises(LLMConfigurationError, match="PRICE"):  # dollar budget but no pricing and no request cap
        LLMSettings.from_env({"SWARMGUARD_LLM_PROVIDER": "openai", "OPENAI_API_KEY": FAKE_KEY,
                              "SWARMGUARD_OPENAI_MODEL": "m"}).validate()
    with pytest.raises(LLMConfigurationError):
        build_provider(LLMSettings(provider="openai"))  # fails before any client exists
    with pytest.raises(LLMConfigurationError):
        LLMSettings(max_retries=9).validate()


def test_openai_provider_success_uses_structured_outputs_and_records_usage():
    sent: list = []
    p = OpenAIProvider(openai_settings(), client=fake_openai(ok_handler(json.dumps(judgment(evidence=["event_1"])), sent)))
    r = run(p.generate_structured(**KW))
    assert r.success and not r.is_synthetic and r.provider_request_id == "req_live_1"
    assert (r.usage.available, r.usage.input_tokens, r.usage.output_tokens) == (True, 120, 40)
    body = sent[0]
    assert body["instructions"] == "sys" and body["input"] == '{"x":1}' and body["store"] is False
    assert body["model"] == "test-model" and body["max_output_tokens"] == 100 and "temperature" not in body
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True


def test_openai_malformed_output_rejected():
    p = OpenAIProvider(openai_settings(), client=fake_openai(ok_handler('{"risk_level":"HIGH"}')))
    r = run(p.generate_structured(**KW))
    assert not r.success and r.error.kind is LLMErrorKind.INVALID_RESPONSE and r.usage.available


@pytest.mark.parametrize("status,message,code,kind", [
    (401, f"Incorrect API key provided: {FAKE_KEY}", "invalid_api_key", LLMErrorKind.AUTHENTICATION),
    (429, "Rate limit reached", "rate_limit_exceeded", LLMErrorKind.RATE_LIMIT),
    (400, "This model's maximum context length is 128000 tokens", "context_length_exceeded", LLMErrorKind.CONTEXT_LENGTH),
    (400, "Unsupported parameter: 'temperature' is not supported with this model.", "unsupported_parameter",
     LLMErrorKind.UNSUPPORTED_CONFIGURATION),
    (404, "The model `nope` does not exist", "model_not_found", LLMErrorKind.UNSUPPORTED_CONFIGURATION),
    (500, "server error", None, LLMErrorKind.PROVIDER_ERROR),
])
def test_openai_errors_are_classified(status, message, code, kind):
    p = OpenAIProvider(openai_settings(), client=fake_openai(error_handler(status, message, code)))
    r = run(p.generate_structured(**{**KW, "temperature": 0.0}))
    assert not r.success and r.error.kind is kind and r.error.status_code == status


def test_7_api_key_never_logged_or_returned(caplog):
    caplog.set_level(logging.DEBUG)
    s = openai_settings()
    p = OpenAIProvider(s, client=fake_openai(error_handler(401, f"Incorrect API key provided: {FAKE_KEY}")))
    r = run(p.generate_structured(**KW))
    blob = " ".join([caplog.text, r.model_dump_json(), repr(p), repr(s), json.dumps(s.public_dict())])
    assert FAKE_KEY not in blob and "sk-test" not in blob and s.public_dict()["api_key_configured"] is True


def test_9_unsupported_settings_recorded_not_silently_dropped():
    sent: list = []
    p = OpenAIProvider(openai_settings(), client=fake_openai(ok_handler(json.dumps(judgment()), sent)))
    r = run(p.generate_structured(**{**KW, "seed": 7, "temperature": 0.0}))
    assert any("seed" in s for s in r.settings_unsupported) and "seed" not in sent[0]
    assert sent[0]["temperature"] == 0.0 and r.settings_applied["temperature"] == 0.0


def test_10_retry_limits_enforced():
    calls: list = []
    p = OpenAIProvider(openai_settings(), client=fake_openai(error_handler(429, "Rate limit", counter=calls)))
    r = run(RetryingProvider(p, max_retries=2, sleep=_no_sleep).generate_structured(**KW))
    assert r.attempts == 3 and len(calls) == 3 and r.error.kind is LLMErrorKind.RATE_LIMIT
    calls.clear()
    p2 = OpenAIProvider(openai_settings(), client=fake_openai(error_handler(401, "bad key", counter=calls)))
    r2 = run(RetryingProvider(p2, max_retries=2, sleep=_no_sleep).generate_structured(**KW))
    assert r2.attempts == 1 and len(calls) == 1  # non-retryable errors are not retried
    queued = MockLLMProvider(MockScript(default=MockStep(response=judgment()),
                                        queues={"m1": [MockStep(MockBehavior.TIMEOUT)]}))
    r3 = run(RetryingProvider(queued, max_retries=1, sleep=_no_sleep).generate_structured(**{**KW, "tags": {"monitor_id": "m1"}}))
    assert r3.success and r3.attempts == 2


def test_budget_reserves_before_requests_and_caps_attempts():
    mock = _mock(MockStep(response=judgment(), usage=(10, 5)))
    # reservation (bytes + overhead) * $1/Mtok + 100 * $1/Mtok must fit; set a budget that fits exactly one call
    policy = BudgetPolicy(max_cost_usd=0.0002, price_input_per_mtok=1.0, price_output_per_mtok=1.0)
    g = GuardedProvider(mock, policy)
    first = run(g.generate_structured(**KW))
    assert first.success and first.reserved_cost_usd is not None and first.estimated_cost_usd == pytest.approx(15e-6)
    g2 = GuardedProvider(_mock(MockStep(response=judgment())), BudgetPolicy(max_cost_usd=1e-9, price_input_per_mtok=1.0,
                                                                            price_output_per_mtok=1.0))
    refused = run(g2.generate_structured(**KW))
    assert refused.error.kind is LLMErrorKind.BUDGET_EXCEEDED and refused.attempts == 0 and g2.exhausted
    assert g2.ledger.attempts == 0 and not g2._inner.calls  # no request was made
    capped = GuardedProvider(_mock(MockStep(response=judgment())), BudgetPolicy(max_requests=2))
    results = [run(capped.generate_structured(**KW)) for _ in range(3)]
    assert [x.success for x in results] == [True, True, False] and results[2].error.kind is LLMErrorKind.REQUEST_LIMIT
    # unknown usage is settled conservatively at the full reservation
    g3 = GuardedProvider(_mock(MockStep(response=judgment())), BudgetPolicy(max_cost_usd=1.0, price_input_per_mtok=1.0,
                                                                            price_output_per_mtok=1.0))
    r = run(g3.generate_structured(**KW))
    assert g3.ledger.spent_usd == pytest.approx(r.reserved_cost_usd) and g3.ledger.reserved_usd == pytest.approx(0)


def test_concurrency_is_bounded():
    slow = MockLLMProvider(MockScript(default=MockStep(MockBehavior.HANG, response=judgment())), hang_seconds=0.02)
    g = GuardedProvider(slow, BudgetPolicy(), max_concurrency=2)

    async def many():
        return await asyncio.gather(*(g.generate_structured(**{**KW, "request_id": f"r{i}"}) for i in range(6)))

    results = run(many())
    assert all(r.success for r in results) and g.max_in_flight_observed == 2
