"""The second tiering stage — ADR 0002.

The router decides how much scrutiny a change gets, and until now the rules were declared in
`kuwarden.yaml` and applied nowhere: `high_paths`, `medium_paths` and `high_changed_files`
were read into config and never consulted. These cover the rules themselves. Invariant 5 —
that a tier may only ever be raised — is covered in `test_invariants.py` and holds over this
by construction, because the caller applies `raise_to` to whatever is returned here.
"""

from __future__ import annotations

from engine.policy.tiering import raise_to, required_approvals, tier_for_diff
from engine.state import Diff, FileChange, RiskRules

RULES = RiskRules(
    high_paths=("**/auth/**", "**/payments/**", "**/migrations/**"),
    medium_paths=("src/**",),
    high_changed_files=3,
)


def _diff(*paths: str) -> Diff:
    return Diff(files=[FileChange(path=p, added=1, removed=0) for p in paths])


def test_a_sensitive_path_is_high_whatever_else_is_true() -> None:
    tier, reason = tier_for_diff("low", _diff("src/payments/charge.ts"), RULES)
    assert tier == "high"
    assert "payments" in reason


def test_a_large_change_is_high_whatever_it_touched() -> None:
    """Size alone. A change nobody can review in one sitting is not low risk."""
    tier, reason = tier_for_diff("low", _diff("a.md", "b.md", "c.md", "d.md"), RULES)
    assert tier == "high"
    assert "4 files changed" in reason


def test_an_ordinary_source_change_is_medium() -> None:
    """The tier that makes scenario two work: one approver, not two, and not none."""
    tier, reason = tier_for_diff("low", _diff("src/app/page.tsx"), RULES)
    assert tier == "medium"
    assert required_approvals(tier) == 1


def test_a_change_no_rule_names_keeps_its_provisional_tier() -> None:
    tier, reason = tier_for_diff("low", _diff("README.md"), RULES)
    assert tier == "low"
    assert reason == "no rule escalated it"
    assert required_approvals(tier) == 0


def test_empty_rules_escalate_nothing() -> None:
    """The honest default, and the one that must not silently gate everything."""
    tier, _ = tier_for_diff("low", _diff("src/auth/login.ts"), RiskRules())
    assert tier == "low"


def test_no_diff_escalates_nothing() -> None:
    assert tier_for_diff("low", None, RULES)[0] == "low"


def test_high_wins_over_medium_when_both_match() -> None:
    """Ordered highest-first and returning on the first match.

    `src/payments/charge.ts` matches `src/**` too. Reporting it medium because a later rule
    also matched would halve the number of humans who look at a payments change.
    """
    tier, reason = tier_for_diff("low", _diff("src/payments/charge.ts"), RULES)
    assert tier == "high"
    assert "medium" not in reason


def test_size_outranks_a_medium_path() -> None:
    big = _diff("src/a.ts", "src/b.ts", "src/c.ts", "src/d.ts")
    tier, reason = tier_for_diff("low", big, RULES)
    assert tier == "high"
    assert "files changed" in reason


def test_the_rules_may_not_lower_a_tier_already_raised() -> None:
    """Invariant 5, at the point this function feeds.

    A ticket labelled `security` arrives high. A one-file README change proposes `low`, and
    the caller's `raise_to` is what refuses it — a model or a rule arguing its way into a
    weaker gate is the failure the invariant names.
    """
    proposed, _ = tier_for_diff("high", _diff("README.md"), RULES)
    assert raise_to("high", proposed) == "high"


def test_a_moderately_sized_change_is_medium() -> None:
    """The threshold that makes one approver reachable by size alone.

    Without it the only size rule was `high_changed_files`, so a change could be small enough
    to need nobody or large enough to need two, with no middle — and "one person should look
    at this" is the most common honest answer.
    """
    rules = RiskRules(medium_changed_files=2, high_changed_files=6)
    tier, reason = tier_for_diff("low", _diff("a.ts", "b.ts", "c.ts"), rules)
    assert tier == "medium"
    assert required_approvals(tier) == 1
    assert "above medium_changed_files" in reason


def test_the_high_threshold_still_outranks_the_medium_one() -> None:
    rules = RiskRules(medium_changed_files=2, high_changed_files=4)
    tier, _ = tier_for_diff("low", _diff("a", "b", "c", "d", "e"), rules)
    assert tier == "high"
    assert required_approvals(tier) == 2


def test_at_the_threshold_is_not_over_it() -> None:
    """`above` means strictly above. Off-by-one here silently gates every change."""
    rules = RiskRules(medium_changed_files=2)
    assert tier_for_diff("low", _diff("a.ts", "b.ts"), rules)[0] == "low"
    assert tier_for_diff("low", _diff("a.ts", "b.ts", "c.ts"), rules)[0] == "medium"
