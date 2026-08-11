"""Shared rules for pulling a repository tree.

The exclusion matching reuses the same glob translator as `protected_paths` rather than a
second implementation, because two glob engines in one codebase eventually disagree — and one
of them decides what an agent may write.
"""

from __future__ import annotations

from engine.adapters.protocols import RepoTree, TreeLimits
from engine.errors import AdapterError
from engine.policy.protected_paths import ProtectedPaths


class TreeTooLarge(AdapterError):
    """The repository exceeds a configured bound.

    Raised rather than trimmed. A Coder editing a silently truncated tree writes code against
    a repository that does not exist, and the resulting change is wrong in a way that reads
    as a plausible mistake rather than as a missing file.
    """


def excluded(path: str, limits: TreeLimits) -> bool:
    """Whether a path is skipped entirely."""
    return ProtectedPaths(patterns=limits.exclude).matches(path) is not None


def check_file_count(count: int, limits: TreeLimits, repo: str) -> None:
    if count > limits.max_files:
        raise TreeTooLarge(
            f"{repo} has {count} files after exclusions, over the {limits.max_files} limit. "
            "Raise sandbox tree limits deliberately, or narrow workspace.repos — a partial "
            "tree is worse than a refused one."
        )


def check_file_size(path: str, size: int, limits: TreeLimits) -> None:
    if size > limits.max_file_bytes:
        raise TreeTooLarge(
            f"{path} is {size} bytes, over the {limits.max_file_bytes} per-file limit"
        )


def finish(commit: str, files: dict[str, bytes], limits: TreeLimits, repo: str) -> RepoTree:
    """Final total check, then the tree."""
    tree = RepoTree(commit=commit, files=files)
    if tree.total_bytes > limits.max_total_bytes:
        raise TreeTooLarge(
            f"{repo} is {tree.total_bytes} bytes at {commit[:8]}, over the "
            f"{limits.max_total_bytes} total limit"
        )
    return tree
