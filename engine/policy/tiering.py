"""Risk tier arithmetic.

Invariant 5: `risk_tier` may only be raised, never lowered — by anything, at either stage.
This module exists so that rule is a function with a test rather than a convention every
caller has to remember.
"""

from __future__ import annotations

from engine.errors import RiskTierLowered
from engine.state import RISK_TIER_ORDER, RiskTier


def raise_to(current: RiskTier, proposed: RiskTier) -> RiskTier:
    """Return the higher of the two tiers.

    Deliberately total rather than raising on a lowering attempt: an advisory LLM suggesting
    `low` for a change already tiered `high` is an expected event, not an error, and it is
    simply ignored. Use `assert_not_lowered` where a lowering attempt is a defect.
    """
    return proposed if RISK_TIER_ORDER[proposed] > RISK_TIER_ORDER[current] else current


def assert_not_lowered(current: RiskTier, proposed: RiskTier) -> None:
    """Raise if `proposed` is weaker than `current`.

    For the paths where a lowering attempt means a code defect rather than a suggestion —
    child-run inheritance, and the second tiering stage overwriting the first.
    """
    if RISK_TIER_ORDER[proposed] < RISK_TIER_ORDER[current]:
        raise RiskTierLowered(f"attempted to lower risk_tier from {current!r} to {proposed!r}")


def required_approvals(tier: RiskTier) -> int:
    """How many humans must approve at this tier — ADR 0002.

    `low` is zero by design. Uniform gating is correct for the first ten runs and fatal at a
    hundred, because it turns the platform into a queue in front of a human.
    """
    return {"low": 0, "medium": 1, "high": 2}[tier]
