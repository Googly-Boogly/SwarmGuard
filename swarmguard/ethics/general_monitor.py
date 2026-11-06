"""General-purpose safety monitor: the RQ2 baseline.

Same provider, input, output schema, token limit, sampling and timeout as the four ethical
monitors. Only the perspective block of the prompt differs: a general safety review with no
philosophical framework (``ethics/prompts.py::SPECS[GENERAL_PURPOSE]``). It is deliberately not
weakened.
"""

from __future__ import annotations

from swarmguard.ethics.base import LLMEthicsMonitor
from swarmguard.models.assessments import EthicalFramework


class GeneralSafetyMonitor(LLMEthicsMonitor):
    framework = EthicalFramework.GENERAL_PURPOSE
