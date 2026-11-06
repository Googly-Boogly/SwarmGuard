"""Helpers shared by the Stage 3 tests (offline only)."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import openai

from swarmguard.llm.configuration import LLMSettings
from swarmguard.models.assessments import WorkflowContext
from swarmguard.models.actions import ActionRequest
from tests.stage2_helpers import run_scenario

FAKE_KEY = "sk-test-FAKEKEY1234567890abcdef"


def judgment(rec: str = "ESCALATE", risk: str = "HIGH", evidence: list[str] | None = None, **kw: Any) -> dict:
    return {"risk_level": risk, "recommendation": rec, "uncertainty": kw.pop("uncertainty", "MEDIUM"),
            "concerns": kw.pop("concerns", ["Protected data may reach an unauthorized recipient"]),
            "evidence_event_ids": evidence or [], "legitimate_interpretations": kw.pop("legit", []),
            "insufficient_evidence": kw.pop("insufficient", False), "explanation": kw.pop("explanation", "Test.")}


def scenario_context(scenario_id: str = "distributed_approval_001") -> tuple[Any, ActionRequest, WorkflowContext]:
    sim, r = run_scenario(scenario_id)
    b = sim.observation_builder()
    ctx = b.ethics_context(b.snapshot_for_action(r.critical_action_id), r.critical_action_id)
    return sim, ctx.action, ctx


def openai_settings(**kw: Any) -> LLMSettings:
    from pydantic import SecretStr

    base = dict(provider="openai", model="test-model", api_key=SecretStr(FAKE_KEY), max_requests=50, max_retries=2)
    base.update(kw)
    return LLMSettings(**base)


def fake_openai(handler) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=FAKE_KEY, max_retries=0,
                              http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


def responses_payload(text: str, model: str = "test-model", usage: tuple[int, int] = (120, 40)) -> dict:
    return {"id": "resp_1", "object": "response", "created_at": 1, "status": "completed", "model": model,
            "output": [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": sum(usage),
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}


def ok_handler(text: str, captured: list | None = None):
    def handler(request):
        if captured is not None:
            captured.append(json.loads(request.content))
        return httpx2.Response(200, headers={"x-request-id": "req_live_1"}, json=responses_payload(text))
    return handler


def error_handler(status: int, message: str, code: str | None = None, counter: list | None = None):
    def handler(request):
        if counter is not None:
            counter.append(1)
        return httpx2.Response(status, headers={"x-request-id": "req_err"},
                               json={"error": {"message": message, "type": "invalid_request_error", "code": code,
                                               "param": None}})
    return handler
