"""Did the change earn its green suite, or was the suite made easier?

The most common way an agent manufactures success is to weaken the tests — delete an
assertion, add `skip`, narrow a case until it cannot fail. A verifier that asks a model "were
the tests weakened?" is asking an opinion about the thing most worth having a fact about.

So this is arithmetic over the diff, and it runs *before* any model does. Three signals, each
of which a human reviewer would compute by eye:

**Assertions removed.** The single clearest tell. A change that deletes assertions and reports
a passing suite is reporting that it removed the questions.

**Skips added.** `@pytest.mark.skip`, `@unittest.skip`, `it.skip`, `xit`, `test.skip` — the
same act under six spellings.

**Test churn out of proportion to source churn.** Rewriting the tests and barely touching the
source is what "make the tests match the code" looks like from the outside, and it is the
shape of a change that adjusted the measurement rather than the thing measured.

None of this is conclusive on its own — deleting a test *file* legitimately removes assertions,
and a refactor legitimately churns tests. It is deliberately reported as findings with the
counts attached, so a model or a human decides on numbers rather than on a hunch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: What counts as a test file. Deliberately broad: a file this misses is a file whose
#: assertions are invisible to the check, which is the failure direction that matters.
TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*$|[^/]*_(test|spec)\.[a-z]+$|"
    r"[^/]*\.(test|spec)\.[a-z]+$",
    re.IGNORECASE,
)

#: Python, JS/TS, Java/C#, Go. One pattern per family rather than a parser, because this is a
#: count and a wrong count is visible in the finding rather than silently trusted.
ASSERTION = re.compile(
    r"\bassert\b|\bassertThat\b|\bassert[A-Z]\w*\(|\bexpect\s*\(|\bshould\b\.|"
    r"\bt\.(Error|Fatal|Errorf|Fatalf)\b",
)

SKIP = re.compile(
    r"@pytest\.mark\.skip|@unittest\.skip|pytest\.skip\(|"
    r"\b(it|test|describe)\.skip\b|\bxit\b|\bxdescribe\b|"
    r"@Ignore\b|\[Ignore\]|\bt\.Skip\(",
)


@dataclass(frozen=True)
class TestEvidence:
    """Counts over the diff, and the findings they support.

    `passed` is the verdict; `findings` is why. Both travel — a blocked change whose record
    says only "blocked" cannot be argued with, and a change that passes narrowly should say so.
    """

    assertions_before: int = 0
    assertions_after: int = 0
    skips_added: int = 0
    test_lines_changed: int = 0
    source_lines_changed: int = 0
    findings: list[str] = field(default_factory=list)

    @property
    def assertions_removed(self) -> int:
        return max(0, self.assertions_before - self.assertions_after)

    @property
    def passed(self) -> bool:
        return not self.findings


def _count(text: str, pattern: re.Pattern[str]) -> int:
    """Matches outside comments. A commented-out assertion is not an assertion.

    Crude on purpose — `#` and `//` at the start of a stripped line. A parser per language is
    a large amount of code for a signal that is reported with its numbers attached.
    """
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "/*")):
            continue
        total += len(pattern.findall(line))
    return total


def evaluate(
    before: dict[str, str],
    after: dict[str, str],
    *,
    churn_ratio: float = 4.0,
) -> TestEvidence:
    """Compare the tests as they were with the tests as they are.

    `before` is the pinned base tree, `after` the same paths with the change applied. Only
    paths present in `after` are compared: a file the change did not touch cannot have had its
    assertions removed by it.

    `churn_ratio` is how many times more test churn than source churn is worth a finding.
    Four is a judgement, not a measurement, and it is a parameter so that arguing about it does
    not mean editing this function.
    """
    findings: list[str] = []
    assertions_before = assertions_after = skips_added = 0
    test_lines = source_lines = 0

    for path, new_text in after.items():
        old_text = before.get(path, "")
        changed = abs(len(new_text.splitlines()) - len(old_text.splitlines()))
        if TEST_PATH.search(path):
            test_lines += changed
            assertions_before += _count(old_text, ASSERTION)
            assertions_after += _count(new_text, ASSERTION)
            added = _count(new_text, SKIP) - _count(old_text, SKIP)
            if added > 0:
                skips_added += added
                findings.append(f"{path}: {added} test(s) newly skipped")
        else:
            source_lines += changed

    removed = max(0, assertions_before - assertions_after)
    if removed:
        findings.append(
            f"{removed} assertion(s) removed across the test files this change touched "
            f"({assertions_before} before, {assertions_after} after)"
        )

    # Only when there is source churn to compare against. A pure test change — adding the
    # tests somebody asked for — has no source churn and is not suspicious for that reason.
    if source_lines and test_lines > source_lines * churn_ratio:
        findings.append(
            f"test churn is {test_lines / source_lines:.1f}x source churn "
            f"({test_lines} test lines against {source_lines} source lines); a change that "
            "rewrites the measurement more than the thing measured is worth reading closely"
        )

    return TestEvidence(
        assertions_before=assertions_before,
        assertions_after=assertions_after,
        skips_added=skips_added,
        test_lines_changed=test_lines,
        source_lines_changed=source_lines,
        findings=findings,
    )
