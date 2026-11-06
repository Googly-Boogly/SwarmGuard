"""Execution outcomes the gateway must distinguish. Never collapse UNKNOWN into "not executed"."""
from __future__ import annotations
from swarmguard.models.decisions import ReasonCode


class ExecutionRefused(RuntimeError):
    """Definitively not executed (executor refused, or precondition failed and rolled back)."""
    def __init__(self, reason: ReasonCode, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


class ExecutorUnavailable(ExecutionRefused):
    """Executor unreachable and the authorization was cancelled before consumption: not executed."""
    def __init__(self, detail: str) -> None:
        super().__init__(ReasonCode.EXECUTOR_UNAVAILABLE, detail)


class ExecutionOutcomeUnknown(RuntimeError):
    """Consumption may have happened but no outcome could be established. Never retried automatically."""
