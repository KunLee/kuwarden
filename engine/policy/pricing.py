"""What a run cost, estimated locally from tokens.

One implementation, because there were two — `_estimate_cents` was copied into `coder.py` and
`planner.py`, and the verifiers, which are four of a run's six model calls and most of its
input, counted nothing at all. A spend figure that omits the majority of the spend is worse
than none, because it is believed.

The old formula was `max(1, (input + output * 5) // 100_000)`. On real numbers it reported one
cent for a 123,000-token call and one cent for a 10,000-token call, so the 93% context
reduction it was meant to demonstrate was invisible in it.

**Absolute accuracy is not the goal; comparability is.** Published rates drift and this file
will go stale exactly as `docs/reference/models.md` warns model identifiers do. What does not
drift is the *shape* — output costs several times input, a cache write costs a premium over
input, a cache read costs a small fraction of it. Those ratios are what make "this run cost a
tenth of that one" true even when both absolute figures are wrong.

**An unknown model costs `None`, never zero.** A model absent from the table below is one
nobody has priced, and reporting a confident `0` for it is the same failure this project
argues about everywhere else: a missing measurement must never look like a passing one.
"""

from __future__ import annotations

from dataclasses import dataclass

#: When the rates below were last checked against published pricing. Same discipline as
#: `docs/reference/models.md`: if this is more than three months old, treat every figure here
#: as unverified rather than as fact.
LAST_REVIEWED = "2026-08-29"

#: Micro-cents per token, so accumulation is exact integer arithmetic and a single cheap call
#: is not rounded up to a whole cent. 1 cent = 1,000,000 micro-cents.
MICRO_CENTS_PER_CENT = 1_000_000


@dataclass(frozen=True)
class Rate:
    """Micro-cents per token, by token class.

    Four classes rather than two, because caching is the thing most likely to be measured with
    this and the two cache classes are where its effect lives. A cache *write* costs more than
    an ordinary input token and a *read* costs a fraction of one — so a workload that writes
    the cache and never reads it is more expensive than not caching at all, and a cost model
    that ignored the distinction could not show that.
    """

    input: int
    output: int
    cache_write: int
    cache_read: int

    @classmethod
    def per_million_dollars(cls, dollars_in: float, dollars_out: float) -> Rate:
        """Build from the usual published shape: dollars per million tokens.

        Cache multipliers are applied here rather than being listed per model because they are
        a property of the caching mechanism, not of a model: a write is charged at 1.25x input
        and a read at 0.1x.
        """
        micro_in = round(dollars_in * 100 * MICRO_CENTS_PER_CENT / 1_000_000)
        micro_out = round(dollars_out * 100 * MICRO_CENTS_PER_CENT / 1_000_000)
        return cls(
            input=micro_in,
            output=micro_out,
            cache_write=round(micro_in * 1.25),
            cache_read=round(micro_in * 0.1),
        )


#: Rates by model id. Ids are exactly as `docs/reference/models.md` gives them — never with a
#: date suffix appended.
#:
#: These are estimates for local comparison, not billing. Verify against the provider's own
#: pricing before quoting a figure to anyone.
#:
#: More models than this deployment uses: an absent one costs `None` and poisons the run's
#: whole total, which is correct and a bad thing to trigger by editing one line of
#: `kuwarden.yaml`.
RATES: dict[str, Rate] = {
    "claude-fable-5": Rate.per_million_dollars(10.00, 50.00),
    "claude-opus-5": Rate.per_million_dollars(5.00, 25.00),
    "claude-opus-4-8": Rate.per_million_dollars(5.00, 25.00),
    "claude-sonnet-5": Rate.per_million_dollars(2.00, 10.00),
    "claude-sonnet-4-6": Rate.per_million_dollars(3.00, 15.00),
    "claude-haiku-4-5": Rate.per_million_dollars(1.00, 5.00),
}


def micro_cents(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> int | None:
    """Estimated cost of one model call, or `None` when the model has no rate.

    `input_tokens` is what the provider charged at the ordinary rate — the cache classes are
    reported separately and are *not* included in it, so they are added rather than replacing
    part of it.
    """
    rate = RATES.get(model)
    if rate is None:
        return None
    return (
        input_tokens * rate.input
        + output_tokens * rate.output
        + cache_write_tokens * rate.cache_write
        + cache_read_tokens * rate.cache_read
    )


def accrue(total: int | None, model: str, usage: object) -> int | None:
    """Add one call's cost to a running total, propagating "unknown".

    `usage` is anything carrying the four token counts — in practice a `Completion`. Typed
    loosely so this module stays under `policy/` without importing an adapter.

    Once any call in a run is unpriced the total is `None` for the rest of it, and stays
    `None`. Adding a known cost to an unknown one produces a figure lower than the truth, and
    a spend report that understates is worse than one that abstains.
    """
    call = micro_cents(
        model,
        input_tokens=int(getattr(usage, "input_tokens", 0)),
        output_tokens=int(getattr(usage, "output_tokens", 0)),
        cache_write_tokens=int(getattr(usage, "cache_write_tokens", 0)),
        cache_read_tokens=int(getattr(usage, "cache_read_tokens", 0)),
    )
    if total is None or call is None:
        return None
    return total + call


def combine(*totals: int | None) -> int | None:
    """Sum totals produced by separate activities, propagating "unknown".

    The verifier fan-out is four activities, each accruing onto its own redacted brief, and
    the flow adds them up afterwards. If any one of them met an unpriced model the sum is
    unknown — a total that quietly omits one of four calls understates by up to a quarter and
    looks authoritative doing it.
    """
    if any(total is None for total in totals):
        return None
    return sum(total for total in totals if total is not None)


def as_text(total: int | None) -> str:
    """A spend figure for a human, in the smallest unit that is not misleading.

    Sub-cent runs are the common case once context is selected, and rounding them to "0 cents"
    would hide exactly the improvement this is used to measure.
    """
    if total is None:
        return "unknown — no rate recorded for this model"
    if total == 0:
        return "0"
    cents = total / MICRO_CENTS_PER_CENT
    if cents < 1:
        return f"{cents:.3f} cents"
    if cents < 100:
        return f"{cents:.2f} cents"
    return f"${cents / 100:.2f}"
