"""Paths an agent identity may never write — ADR 0004 §1.

Unconditional and model-independent. CI definitions live inside the branch the Coder can
write, and a workflow file is executable on push, so an agent that can write code could
otherwise run arbitrary code with CI credentials — reachable by prompt injection through
ticket content.

This is a deny, not a tier escalation, and a match fails the run rather than dropping the
file, so the attempt stays visible in the audit record.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.errors import ProtectedPathWritten
from engine.policy.globs import matches_any

# Mirrors policy.yaml. Until the schema loader exists this is the enforced copy, and the
# test asserts the two have not drifted apart.
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    ".github/workflows/**",
    ".github/actions/**",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "Jenkinsfile",
    "charts/**",
    "terraform/**",
    "**/*.tfvars",
    "**/kuwarden.yaml",
    "policy.yaml",
)


@dataclass(frozen=True)
class ProtectedPaths:
    patterns: tuple[str, ...] = DEFAULT_PROTECTED_PATHS

    def matches(self, path: str) -> str | None:
        """Return the pattern that denies `path`, or None."""
        return matches_any(self.patterns, path)

    def violations(self, paths: list[str]) -> list[tuple[str, str]]:
        """Every (path, pattern) pair that must fail the run."""
        found = [(p, m) for p in paths if (m := self.matches(p)) is not None]
        return found


def assert_not_protected(paths: list[str]) -> None:
    """Fail the run if the diff touches a path agents may never write — invariant 10.

    One function, called from Push and again from Build & Test. Two nodes enforce the same
    rule and neither may own a second copy of it: copies drift, and drift here is invisible —
    both copies keep passing their own tests while denying different sets of paths.
    """
    violations = ProtectedPaths().violations(paths)
    if violations:
        raise ProtectedPathWritten(", ".join(f"{path} ({pattern})" for path, pattern in violations))
