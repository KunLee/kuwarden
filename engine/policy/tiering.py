"""Risk tier arithmetic.

Invariant 5: `risk_tier` may only be raised, never lowered — by anything, at either stage.
This module exists so that rule is a function with a test rather than a convention every
caller has to remember.
"""

from __future__ import annotations

from engine.errors import RiskTierLowered
from engine.policy.globs import matches_any
from engine.state import RISK_TIER_ORDER, Diff, RiskRules, RiskTier


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


def tier_for_diff(current: RiskTier, diff: Diff | None, rules: RiskRules) -> tuple[RiskTier, str]:
    """The second tiering stage — ADR 0002. Returns the tier and the reason for it.

    Rules-first and over the *actual diff*, which is why this cannot run at intake: at ①
    there is no diff to read, and a tier assigned from a ticket description is a guess. This
    is the authoritative stage, and the only one whose answer sets gate depth.

    The reason travels with the tier because an approver seeing "this needs two of you" is
    owed the rule that decided it. A gate whose depth nobody can account for is one people
    learn to clear without reading.

    Pure, and takes rules as an argument rather than reading configuration: it is called from
    workflow code, where a replay must reach the same answer from the same inputs.

    Never lowers. `raise_to` is applied by the caller, but the checks here are ordered
    highest-first and return on the first match, so a `high` path is never reported as
    `medium` because a later rule also matched.
    """
    paths = diff.paths if diff else []

    for path in paths:
        if (pattern := matches_any(rules.high_paths, path)) is not None:
            return "high", f"{path} matches high_paths {pattern!r}"

    # Size, before medium paths: a change large enough to be unreviewable is high whatever it
    # touched, and reporting it as medium because one file matched a medium glob would
    # understate it.
    if rules.high_changed_files is not None and len(paths) > rules.high_changed_files:
        return (
            "high",
            f"{len(paths)} files changed, above high_changed_files "
            f"({rules.high_changed_files})",
        )

    for path in paths:
        if (pattern := matches_any(rules.medium_paths, path)) is not None:
            return "medium", f"{path} matches medium_paths {pattern!r}"

    if rules.medium_changed_files is not None and len(paths) > rules.medium_changed_files:
        return (
            "medium",
            f"{len(paths)} files changed, above medium_changed_files "
            f"({rules.medium_changed_files})",
        )

    return current, "no rule escalated it"
