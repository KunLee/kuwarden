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


class PermissionDenied(AdapterError):
    """The credential reached the platform and was refused this specific action.

    Distinguished from the rest of `AdapterError` so a caller can say *which grant is
    missing*. Platforms answer with their own wording — GitHub's is "Resource not accessible
    by personal access token" — which is true and leaves the operator to guess which of a
    dozen permission toggles it meant.
    """


class NotFound(AdapterError):
    """The external system answered, and the thing asked for does not exist.

    Separated from the rest of `AdapterError` because absence is frequently a *normal* state —
    a branch this run has not created yet — and a caller that cannot tell "not there" from
    "could not reach it" has to treat a network failure as an absence, which is how a retry
    turns into a second push.
    """
