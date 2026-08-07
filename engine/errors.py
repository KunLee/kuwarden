"""Typed errors. Never `except Exception: pass`.

The distinction that matters most here is `SandboxInfrastructureError` versus everything
else: a sandbox that failed is not a change that failed, and charging it a retry teaches the
Coder the wrong lesson while burning budget on an unfixable problem — ADR 0005 §5.
"""

from __future__ import annotations


class KuWardenError(Exception):
    """Base for everything raised deliberately by the engine."""


class InvariantViolation(KuWardenError):
    """An invariant in CLAUDE.md was violated. Never caught — it means a code defect."""


class RiskTierLowered(InvariantViolation):
    """Something attempted to lower `risk_tier`. Permitted for nothing, at either stage."""


class ProtectedPathWritten(KuWardenError):
    """A diff touched a path agents may never write.

    Fails the run rather than dropping the file, so the attempt stays visible — ADR 0005 §3.
    """


class PolicyDenied(KuWardenError):
    """A privileged action was denied by the pinned policy, the current policy, or both."""


class GateRejected(KuWardenError):
    """A human rejected the change at an approval gate."""


class SandboxInfrastructureError(KuWardenError):
    """The sandbox itself failed. Does not consume a retry — ADR 0005 §5."""


class AdapterError(KuWardenError):
    """An external system of record could not be reached or answered unusably."""
