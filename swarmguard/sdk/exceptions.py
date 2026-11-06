"""Structured SDK errors. Gateway BLOCK/ESCALATE outcomes are decisions, not exceptions."""
from __future__ import annotations


class SwarmGuardSDKError(RuntimeError):
    """Base class for host-side SDK failures."""


class FrameworkUnavailableError(SwarmGuardSDKError):
    """The optional framework dependency is missing or outside the supported version family."""
