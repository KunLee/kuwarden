"""The one glob implementation in this codebase.

Extracted so that path-matching policy — which paths agents may never write (invariant 10),
which paths raise a risk tier (ADR 0002) — is decided by identical semantics everywhere. Two
implementations would each keep passing their own tests while disagreeing about `charts/*`,
and the disagreement would be invisible until it mattered.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def translate(pattern: str) -> re.Pattern[str]:
    """Glob to regex, with `**` crossing separators and `*` not.

    `fnmatch` is not usable here: its `*` matches `/`, which would make `charts/*` silently
    equivalent to `charts/**` and quietly widen or narrow a security control.

    Cached because the same handful of patterns are matched against every path in every diff,
    and recompiling per path made the protected-path check quadratic in the size of a change.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def normalise(path: str) -> str:
    """A repository path as the matchers expect it.

    `removeprefix`, not `lstrip`: lstrip takes a character set, so it would strip the leading
    dot from `.github/...` and quietly unprotect every dotfile path.
    """
    return path.replace("\\", "/").removeprefix("./")


def matches_any(patterns: tuple[str, ...] | list[str], path: str) -> str | None:
    """Return the first pattern matching `path`, or None.

    Returns the pattern rather than a bool because every caller has to explain itself: an
    audit row saying "denied" is not reviewable, and one naming the rule that denied it is.
    """
    candidate = normalise(path)
    for pattern in patterns:
        if translate(pattern).match(candidate):
            return pattern
    return None
