"""Council builders: four specialized ethics monitors vs. four (or one) general-purpose monitors.

The four general monitors share one prompt and one provider. Their only possible source of
variation is the provider's own sampling (recorded in the configuration). No diversity is
injected. With deterministic decoding they may answer identically; that correlation is measured
(``disagreement.within_council_agreement``) and not hidden.
"""

from __future__ import annotations

from swarmguard.ethics.aggregator import AggregationMethod, Aggregator
from swarmguard.ethics.care import CareEthicsMonitor
from swarmguard.ethics.consequentialism import ConsequentialistMonitor
from swarmguard.ethics.council import EthicsCouncil
from swarmguard.ethics.deontology import DeontologicalMonitor
from swarmguard.ethics.general_monitor import GeneralSafetyMonitor
from swarmguard.ethics.virtue import VirtueEthicsMonitor
from swarmguard.llm.base import LLMProvider

SPECIALIZED = "SPECIALIZED_ETHICS"
GENERAL = "GENERAL_PURPOSE"


def _common(max_output_tokens: int, temperature: float | None, seed: int | None) -> dict:
    return {"max_output_tokens": max_output_tokens, "temperature": temperature, "seed": seed}


def build_specialized_council(provider: LLMProvider, *, aggregator: Aggregator | None = None,
                              timeout_seconds: float = 60.0, max_concurrency: int = 4, max_output_tokens: int = 800,
                              temperature: float | None = None, seed: int | None = None) -> EthicsCouncil:
    kw = _common(max_output_tokens, temperature, seed)
    monitors = [VirtueEthicsMonitor(provider, **kw), ConsequentialistMonitor(provider, **kw),
                DeontologicalMonitor(provider, **kw), CareEthicsMonitor(provider, **kw)]
    return EthicsCouncil(monitors, timeout_seconds=timeout_seconds, council_type=SPECIALIZED,
                         aggregator=aggregator or Aggregator(AggregationMethod.MAJORITY_WITH_ESCALATION),
                         max_concurrency=max_concurrency)


def build_general_council(provider: LLMProvider, *, size: int = 4, aggregator: Aggregator | None = None,
                          timeout_seconds: float = 60.0, max_concurrency: int = 4, max_output_tokens: int = 800,
                          temperature: float | None = None, seed: int | None = None) -> EthicsCouncil:
    kw = _common(max_output_tokens, temperature, seed)
    monitors = [GeneralSafetyMonitor(provider, monitor_id=f"llm_general_purpose_{i}", **kw) for i in range(1, size + 1)]
    return EthicsCouncil(monitors, timeout_seconds=timeout_seconds, council_type=f"{GENERAL}_X{size}",
                         aggregator=aggregator or Aggregator(AggregationMethod.MAJORITY_WITH_ESCALATION),
                         required_frameworks=(), max_concurrency=max_concurrency)
