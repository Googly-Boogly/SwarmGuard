"""Test double for openai.AsyncOpenAI (offline). Answers with the synthetic rules responder and reports usage."""
import json
from types import SimpleNamespace
from swarmguard.ethics.synthetic import rules_responder

class FakeOpenAI:
    """Records every request; optionally fails every n-th call."""
    def __init__(self, fail_every: int = 0):
        self.calls = []
        self.fail_every = fail_every
        outer = self
        class Raw:
            async def parse(self, **kw):
                outer.calls.append(kw)
                if outer.fail_every and len(outer.calls) % outer.fail_every == 0:
                    raise RuntimeError("simulated provider outage")
                answer = rules_responder(kw["instructions"], kw["input"], {})
                answer["explanation"] = "Fake live model: " + answer["explanation"]
                body = {"usage": {"input_tokens": len(kw["input"]) // 4, "output_tokens": 120, "total_tokens": len(kw["input"]) // 4 + 120}}
                return SimpleNamespace(headers={"x-request-id": f"req_{len(outer.calls)}"},
                                       http_response=SimpleNamespace(json=lambda: body),
                                       parse=lambda: SimpleNamespace(output_parsed=kw["text_format"].model_validate(answer), status="completed"))
        self.responses = SimpleNamespace(with_raw_response=Raw())
