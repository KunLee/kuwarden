"""Where a path supplied by a model is allowed to land.

One function, for the same reason [`globs.py`](globs.py) is one function: a confinement check
written twice is two rules that each keep passing their own tests while disagreeing about what
they permit, and the disagreement stays invisible until the day it matters. The Coder writes
files from a model's JSON today; [ADR 0011](../../docs/adr/0011-tool-based-retrieval.md) adds
`read_file`, `grep` and `list_dir` over the same workspace, and every one of them needs this
exact check rather than its own version of it.

The threat is named rather than theoretical. Ticket text is hostile by assumption — anyone who
can file a ticket can write it, and it reaches a model — and `../../.ssh/id_rsa` is precisely
what a successful prompt injection asks for. The sandbox mounts only the workspace, but these
paths are resolved on the **host** side of that boundary, in the Coder activity, before any
container exists. The container's isolation is not a substitute for this check.
"""

from __future__ import annotations

from pathlib import Path

from engine.errors import SandboxInfrastructureError


def confine(root: Path, path: str) -> Path:
    """Resolve `path` inside `root`, or refuse it. Callers use the value returned.

    `resolve()` runs before the comparison. It collapses `..` and follows symlinks, so a link
    planted inside the workspace pointing out of it is caught rather than written through —
    the case a string-prefix check misses. An absolute path needs no branch of its own:
    `root / "/etc/passwd"` is `/etc/passwd`, which is not relative to the root.
    """
    if not path.strip():
        # Otherwise this resolves to the workspace root itself and passes, handing the caller
        # a directory where it expects a file. Refused here so the message names the cause.
        raise SandboxInfrastructureError("refusing a workspace path that is empty")

    base = root.resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise SandboxInfrastructureError(
            f"refusing to touch a path outside the workspace: {path!r}"
        )
    return resolved
