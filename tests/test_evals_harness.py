"""The evaluation harness loads its cases — deterministic, free, no model calls.

The harness itself spends money by design; this covers the part that must not be allowed to
break silently. A case file that fails to parse is a case that stops protecting something, and
nobody would notice from a green suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness import CATEGORIES, load

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


def test_every_shipped_case_loads() -> None:
    cases = load(CASES)
    assert cases, "the golden set is empty"
    for case in cases:
        assert case.category in CATEGORIES
        assert case.edits, f"{case.id} constructs no diff, so there is nothing to judge"
        assert case.rationale, f"{case.id} records no rationale"


def test_the_set_contains_cases_that_must_be_rejected() -> None:
    """The property that makes the set worth running.

    A suite of happy-path cases cannot distinguish a working verifier from one that returns
    "fine" unconditionally, because both pass every one of them.
    """
    cases = load(CASES)
    rejecting = [c for c in cases if c.expect == "rejected"]
    assert rejecting, "a set with no reject cases measures nothing"


def test_the_set_contains_a_control_that_must_pass() -> None:
    """Without one, a verifier that rejects everything scores perfectly."""
    assert [c for c in load(CASES) if c.expect == "accepted"]


def test_a_malformed_case_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("id: x\ncategory: reject\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load(tmp_path)


def test_an_unknown_category_is_refused(tmp_path: Path) -> None:
    (tmp_path / "odd.yaml").write_text(
        "id: x\ncategory: maybe\nrationale: r\nverifiers: [security]\n"
        "expect: rejected\nticket: {id: T, title: t, body: b}\nedits: [{path: a, content: c}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown category"):
        load(tmp_path)
