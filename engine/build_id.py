"""What code a process started with, so drift from the repository becomes visible.

A worker that has loaded stale modules is invisible from outside. It connects, it polls, it
accepts tasks, and it fails every one of them — and nothing reports that its code disagrees
with the repository. That cost three hours on 2026-08-31: an edit added an import to
`delivery.py` while a worker was running, the process reloaded that module and not the one it
imported from, and every workflow task failed on `cannot import name 'ChangedFile'`. The files
on disk were consistent the whole time; `mypy --strict` and the full suite passed. **The
inconsistency existed only in a running process, which is the one place neither of those
looks.**

The signal is therefore not "what does the tree say" — that agreed throughout the outage — but
**"what did this process start with, and has the tree moved since"**. `build_id()` is computed
once and frozen for the life of the process; `current()` re-reads. When they differ, the
process is older than the code, which is exactly the condition that produced the outage.

Not a version and not human-meaningful. Two values agree or they do not, and that is the whole
question it answers.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: The package that defines this process's behaviour. Only `engine`: the UI is served
#: separately and a dependency's version is a different question, answered by the lock file.
ROOT = Path(__file__).resolve().parent

_FROZEN: str | None = None


def _digest() -> str:
    """Hash every `.py` under `engine/`, in sorted order so two processes agree.

    Path-relative and sorted, because two checkouts at different absolute paths are running
    the same code and must produce the same value.
    """
    h = hashlib.sha256()
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        h.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        try:
            h.update(path.read_bytes())
        except OSError:
            # A file that cannot be read is itself a disagreement, and recording a marker keeps
            # the digest stable rather than letting the failure silently shorten the input.
            h.update(b"<unreadable>")
    return h.hexdigest()[:12]


def build_id() -> str:
    """The code this process started with. Frozen on first call.

    Call it once at startup, before anything can reload a module. Freezing is the point: a
    process that has half-reloaded is precisely the failure being detected, and it must not be
    able to re-hash its way back into agreement with the tree.
    """
    global _FROZEN
    if _FROZEN is None:
        _FROZEN = _digest()
    return _FROZEN


def current() -> str:
    """What the tree says now. Recomputed every call, and compared against `build_id()`."""
    return _digest()
