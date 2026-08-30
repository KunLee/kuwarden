"""One fetch per commit, per worker — `engine.adapters.scm.tree_cache`.

`read_tree` is N+1 requests and a single run asks for the same tree six times: the Coder,
Build & Test, and once per verifier. On the reference application that is ~534 requests for
content that cannot have changed, against a GitHub limit of 5,000 an hour.
"""

from __future__ import annotations

import asyncio

import pytest

from engine.adapters.protocols import RepoRef, RepoTree, TreeLimits
from engine.adapters.scm import tree_cache

REF = RepoRef(host="github.com", org="acme", repo="payments", project=None)


def _tree(commit: str) -> RepoTree:
    return RepoTree(commit=commit, files={"src/app.py": b"x = 1\n"})


async def test_the_same_commit_is_fetched_once() -> None:
    """A commit is content-addressed, so a second fetch cannot return anything different."""
    calls: list[str] = []

    async def fetch() -> RepoTree:
        calls.append("c0ffee12")
        return _tree("c0ffee12")

    limits = TreeLimits()
    first = await tree_cache.cached(REF, "c0ffee12", limits, fetch)
    second = await tree_cache.cached(REF, "c0ffee12", limits, fetch)

    assert calls == ["c0ffee12"], "the second caller was served from the cache"
    assert first is second


async def test_concurrent_callers_share_one_fetch() -> None:
    """The case this exists for, and the one a plain cache does not solve.

    The four verifiers are launched by `asyncio.gather`. Without single-flight all four miss
    at the same instant, all four fetch, and the cache does nothing for the fan-out — which
    is four of the six requests a run makes.
    """
    calls: list[int] = []
    started = asyncio.Event()

    async def fetch() -> RepoTree:
        calls.append(1)
        started.set()
        # Long enough that every sibling has reached the cache before this resolves.
        await asyncio.sleep(0.05)
        return _tree("c0ffee12")

    limits = TreeLimits()
    trees = await asyncio.gather(
        *(tree_cache.cached(REF, "c0ffee12", limits, fetch) for _ in range(4))
    )

    assert len(calls) == 1, "four concurrent callers, one request"
    assert all(t is trees[0] for t in trees)


async def test_a_different_commit_is_a_different_tree() -> None:
    """The key is the commit. Serving one commit's tree for another is the worst outcome
    available here — a Coder editing against a repository that does not exist."""
    async def fetch_a() -> RepoTree:
        return _tree("aaaaaaaa")

    async def fetch_b() -> RepoTree:
        return _tree("bbbbbbbb")

    limits = TreeLimits()
    a = await tree_cache.cached(REF, "aaaaaaaa", limits, fetch_a)
    b = await tree_cache.cached(REF, "bbbbbbbb", limits, fetch_b)

    assert a.commit == "aaaaaaaa"
    assert b.commit == "bbbbbbbb"


async def test_different_limits_are_not_interchangeable() -> None:
    """Limits change the outcome, not merely the cost.

    A bound a tree exceeds makes `read_tree` *raise* — so a tree fetched under generous
    limits must never be handed to a caller who asked for strict ones and would have been
    refused. `TreeLimits` exists to be refused, not silently applied.
    """
    calls: list[str] = []

    async def fetch() -> RepoTree:
        calls.append("fetched")
        return _tree("c0ffee12")

    await tree_cache.cached(REF, "c0ffee12", TreeLimits(), fetch)
    await tree_cache.cached(REF, "c0ffee12", TreeLimits(max_files=1), fetch)

    assert len(calls) == 2, "a stricter bound is a different question, not a cache hit"


async def test_a_failed_fetch_is_not_remembered() -> None:
    """A failure left in the in-flight map would make every later caller await a task that
    can never produce a tree — a transient network error becoming permanent."""
    attempts: list[int] = []

    async def failing() -> RepoTree:
        attempts.append(1)
        raise RuntimeError("github is unreachable")

    limits = TreeLimits()
    with pytest.raises(RuntimeError):
        await tree_cache.cached(REF, "c0ffee12", limits, failing)

    async def succeeding() -> RepoTree:
        attempts.append(1)
        return _tree("c0ffee12")

    tree = await tree_cache.cached(REF, "c0ffee12", limits, succeeding)

    assert len(attempts) == 2, "the retry actually ran"
    assert tree.commit == "c0ffee12"
