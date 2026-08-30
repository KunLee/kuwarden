"""One fetch of a repository tree per commit, per worker.

`read_tree` is N+1 requests — one to list the tree, one per blob — and a single run asks for
the *same* tree six times: the Coder, Build & Test, and once per verifier. On the reference
application that is ~89 requests each, ~534 a run, for content that cannot have changed.

A commit is content-addressed, which makes this the safest kind of cache there is. The tree at
`645b57fb` is the same tree forever, so there is no TTL, no invalidation and no staleness — the
property that makes most caches dangerous simply does not apply. Contrast `engine.config_store`,
which caches on a TTL precisely because configuration mutates under a stable key.

**Single-flight is the point, not an optimisation.** The four verifiers are launched by
`asyncio.gather`, so without it all four miss simultaneously, all four fetch, and the cache
does nothing for the case that needed it most. Concurrent callers for the same commit await one
fetch instead.

In-process, and therefore per worker. Temporal may schedule the Coder on one worker and a
verifier on another, and then neither shares this. A shared cache — Redis or similar — is the
answer to *that*, and it is deliberately not built here: it is infrastructure bought for a
problem a single-worker deployment does not have, and every dependency ships into an air-gapped
environment as somebody's security review.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from engine.adapters.protocols import RepoRef, RepoTree, TreeLimits

log = logging.getLogger(__name__)

#: How many distinct trees to hold. A run touches one commit, so this only needs to span
#: concurrent runs; the bound exists because a tree is hundreds of kilobytes and an unbounded
#: cache in a long-lived worker is a leak with a respectable name.
_MAX_ENTRIES = 4

_TREES: OrderedDict[tuple[str, ...], RepoTree] = OrderedDict()
#: Fetches currently in flight, so concurrent callers for one commit await a single request.
_INFLIGHT: dict[tuple[str, ...], asyncio.Task[RepoTree]] = {}


def _key(ref: RepoRef, commit: str, limits: TreeLimits) -> tuple[str, ...]:
    """Identity of a fetch.

    `limits` is part of it because it changes the outcome rather than merely the cost: a bound
    that a tree exceeds makes `read_tree` *raise*, so a result fetched under generous limits
    must not be served to a caller who asked for strict ones.
    """
    return (
        ref.host,
        ref.org,
        ref.repo,
        ref.project or "",
        commit,
        str(limits.max_files),
        str(limits.max_file_bytes),
        str(limits.max_total_bytes),
        # Excluded paths change *which* files a tree contains, not just whether the fetch is
        # refused, so two limits differing only here are genuinely different trees.
        "|".join(limits.exclude),
    )


def forget() -> None:
    """Drop everything. For tests, which must not inherit another test's tree."""
    _TREES.clear()
    _INFLIGHT.clear()


async def cached(
    ref: RepoRef,
    commit: str,
    limits: TreeLimits,
    fetch: Callable[[], Awaitable[RepoTree]],
) -> RepoTree:
    """Return the tree at `commit`, fetching it at most once per worker.

    `fetch` is passed in rather than the adapter, so this module knows nothing about GitHub or
    Azure Repos and both call it identically — the argument in `engine/policy/globs.py`, where
    two implementations of one rule each kept passing their own tests while disagreeing.
    """
    key = _key(ref, commit, limits)

    hit = _TREES.get(key)
    if hit is not None:
        # Moved to the end so the bound below evicts the least recently used rather than the
        # oldest — within a run the same commit is asked for repeatedly, and evicting it
        # because it was fetched first would defeat the whole point.
        _TREES.move_to_end(key)
        return hit

    running = _INFLIGHT.get(key)
    if running is not None:
        # Someone is already fetching this. `shield` because awaiting a task does not own it:
        # if this caller is cancelled, the fetch must survive for the others waiting on it.
        return await asyncio.shield(running)

    task = asyncio.ensure_future(fetch())
    _INFLIGHT[key] = task
    try:
        tree = await task
    finally:
        # Cleared whether or not the fetch succeeded. A failure left in the map would make
        # every later caller await a task that will never produce a tree.
        _INFLIGHT.pop(key, None)

    _TREES[key] = tree
    while len(_TREES) > _MAX_ENTRIES:
        evicted, _ = _TREES.popitem(last=False)
        log.debug("evicted cached tree %s", evicted[4][:8])
    return tree
