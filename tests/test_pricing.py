"""Estimating what a run cost — `engine.policy.pricing`.

The figure exists to compare runs: did selecting context save anything, is the cache reading
or only writing. So the properties that matter are discrimination and honesty about what is
unknown, not agreement with an invoice to the cent.
"""

from __future__ import annotations

from engine.policy import pricing


class _Usage:
    """A stand-in for `Completion`, which `pricing` deliberately does not import."""

    def __init__(self, i: int, o: int, cw: int = 0, cr: int = 0) -> None:
        self.input_tokens, self.output_tokens = i, o
        self.cache_write_tokens, self.cache_read_tokens = cw, cr


def test_a_large_call_and_a_small_one_are_not_the_same_price() -> None:
    """The defect this replaced, stated as a test.

    `max(1, (input + output * 5) // 100_000)` reported one cent for a 123,000-token call and
    one cent for a 10,000-token call. The 93% context reduction it was meant to demonstrate
    was invisible in the only number anybody was watching.
    """
    big = pricing.micro_cents("claude-sonnet-5", input_tokens=123_298, output_tokens=4_644)
    small = pricing.micro_cents("claude-sonnet-5", input_tokens=10_000, output_tokens=4_644)

    assert big is not None
    assert small is not None
    assert big > small * 3, "an order-of-magnitude difference in input must be visible"


def test_a_cache_read_is_far_cheaper_than_sending_the_tokens_again() -> None:
    """Why the four token classes exist rather than two."""
    resent = pricing.micro_cents("claude-sonnet-5", input_tokens=100_000, output_tokens=0)
    reread = pricing.micro_cents(
        "claude-sonnet-5", input_tokens=0, output_tokens=0, cache_read_tokens=100_000
    )

    assert resent is not None
    assert reread is not None
    assert reread < resent // 5


def test_a_cache_write_costs_more_than_sending_the_tokens_once() -> None:
    """The failure mode that must be visible: writing a cache nothing reads.

    Four verifiers fired by `asyncio.gather` can all miss and all write. If a write were
    priced as ordinary input, that outcome would look free instead of like a surcharge.
    """
    plain = pricing.micro_cents("claude-sonnet-5", input_tokens=100_000, output_tokens=0)
    written = pricing.micro_cents(
        "claude-sonnet-5", input_tokens=0, output_tokens=0, cache_write_tokens=100_000
    )

    assert plain is not None
    assert written is not None
    assert written > plain


def test_an_unpriced_model_costs_unknown_not_zero() -> None:
    """A missing measurement must never read as a passing one — the rule, applied to money."""
    assert pricing.micro_cents("some-model-nobody-priced", input_tokens=1, output_tokens=1) is None
    assert "unknown" in pricing.as_text(None)


def test_unknown_poisons_a_total_rather_than_being_skipped() -> None:
    """Adding a known cost to an unknown one understates, and looks authoritative doing it."""
    total = pricing.accrue(0, "claude-sonnet-5", _Usage(1_000, 100))
    assert total is not None

    total = pricing.accrue(total, "some-model-nobody-priced", _Usage(1_000, 100))
    assert total is None

    # And it stays unknown for the rest of the run.
    assert pricing.accrue(total, "claude-sonnet-5", _Usage(1_000, 100)) is None
    assert pricing.combine(5, None, 7) is None


def test_a_sub_cent_run_is_not_reported_as_zero() -> None:
    """Once context is selected, sub-cent runs are the common case — and rounding them to
    "0 cents" would hide exactly the improvement this is used to measure."""
    tiny = pricing.micro_cents("claude-sonnet-5", input_tokens=556, output_tokens=300)
    assert tiny is not None
    rendered = pricing.as_text(tiny)

    assert rendered != "0"
    assert "cents" in rendered
