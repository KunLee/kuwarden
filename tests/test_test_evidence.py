"""The one verifier signal that is arithmetic rather than an opinion.

"Were the tests weakened?" is the question most worth having a *fact* about, because
weakening them is the most common way an agent manufactures a passing suite. Asking a model
is asking an opinion about exactly the thing that should be counted.
"""

from __future__ import annotations

from engine.policy.test_evidence import evaluate

SUITE = '''
def test_add():
    assert add(1, 2) == 3
    assert add(0, 0) == 0


def test_sub():
    assert sub(3, 1) == 2
'''


def test_an_unchanged_suite_passes() -> None:
    result = evaluate({"tests/test_app.py": SUITE}, {"tests/test_app.py": SUITE})
    assert result.passed
    assert result.assertions_before == result.assertions_after == 3


def test_removing_an_assertion_is_a_finding() -> None:
    """The clearest tell there is: a change that deletes the questions and reports a pass."""
    weakened = SUITE.replace("    assert add(0, 0) == 0\n", "")
    result = evaluate({"tests/test_app.py": SUITE}, {"tests/test_app.py": weakened})

    assert not result.passed
    assert result.assertions_removed == 1
    assert "assertion(s) removed" in result.findings[0]


def test_adding_a_skip_is_a_finding() -> None:
    skipped = SUITE.replace("def test_sub", "@pytest.mark.skip\ndef test_sub")
    result = evaluate({"tests/test_app.py": SUITE}, {"tests/test_app.py": skipped})

    assert not result.passed
    assert result.skips_added == 1
    assert "newly skipped" in result.findings[0]


def test_a_commented_out_assertion_does_not_count_as_one() -> None:
    """Otherwise commenting out an assertion reads as keeping it."""
    commented = SUITE.replace("    assert add(0, 0) == 0", "    # assert add(0, 0) == 0")
    result = evaluate({"tests/test_app.py": SUITE}, {"tests/test_app.py": commented})

    assert result.assertions_removed == 1, "a commented assertion is not an assertion"


def test_adding_tests_is_never_suspicious() -> None:
    """A change that only adds tests has no source churn to be disproportionate to."""
    more = SUITE + "\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
    result = evaluate({"tests/test_app.py": SUITE}, {"tests/test_app.py": more})

    assert result.passed
    assert result.assertions_after > result.assertions_before


def test_test_churn_far_beyond_source_churn_is_a_finding() -> None:
    """Rewriting the measurement more than the thing measured."""
    result = evaluate(
        {"tests/test_app.py": SUITE, "src/app.py": "def add(a, b):\n    return a + b\n"},
        {
            "tests/test_app.py": SUITE + "\n" * 60,
            "src/app.py": "def add(a, b):\n    return a + b\n# tweak\n",
        },
    )
    assert not result.passed
    assert any("churn" in f for f in result.findings)


def test_a_file_the_change_did_not_touch_is_not_compared() -> None:
    """It cannot have had its assertions removed by a change that never opened it."""
    result = evaluate(
        {"tests/test_app.py": SUITE, "tests/test_other.py": SUITE},
        {"tests/test_app.py": SUITE},
    )
    assert result.passed


def test_javascript_and_go_spellings_are_counted() -> None:
    """One family per pattern. A file this misses is a file whose assertions are invisible."""
    before = {
        "src/app.test.ts": "it('works', () => { expect(add(1,2)).toBe(3); });",
        "pkg/app_test.go": 'func TestAdd(t *testing.T) { t.Errorf("x") }',
    }
    after = {
        "src/app.test.ts": "it.skip('works', () => {});",
        "pkg/app_test.go": "func TestAdd(t *testing.T) {}",
    }
    result = evaluate(before, after)

    assert not result.passed
    assert result.skips_added == 1
    assert result.assertions_removed == 2
