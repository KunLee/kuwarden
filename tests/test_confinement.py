"""Path confinement — `engine.policy.confinement`.

The rule was inline in `coder.py` and is about to have four more callers: ADR 0011's
`read_file`, `grep`, `list_dir` and `edit_file`. These tests exist so the shared function is
the one that gets extended, rather than each tool growing its own almost-identical check —
which is how `globs.py` came to say what it says about two implementations of one rule.

Every case here is something a model can put in a JSON field, and ticket text is hostile by
assumption.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.errors import SandboxInfrastructureError
from engine.policy.confinement import confine


def test_an_ordinary_path_resolves_inside_the_workspace(tmp_path: Path) -> None:
    """The permitted case, and the returned value is what callers must use."""
    resolved = confine(tmp_path, "src/app/page.tsx")

    assert resolved == (tmp_path / "src" / "app" / "page.tsx").resolve()
    assert resolved.is_relative_to(tmp_path.resolve())


def test_traversal_out_of_the_workspace_is_refused(tmp_path: Path) -> None:
    """`../../.ssh/id_rsa` is the literal thing a successful prompt injection asks for."""
    with pytest.raises(SandboxInfrastructureError, match="outside the workspace"):
        confine(tmp_path, "../../.ssh/id_rsa")


def test_an_absolute_path_is_refused(tmp_path: Path) -> None:
    """No branch of its own: `root / "/etc/passwd"` is `/etc/passwd`, which fails the same test.

    Worth a case anyway, because the day someone replaces `/` with a string join is the day
    that stops being true and nothing else would notice.
    """
    with pytest.raises(SandboxInfrastructureError, match="outside the workspace"):
        confine(tmp_path, "/etc/passwd")


def test_a_symlink_pointing_out_of_the_workspace_is_refused(tmp_path: Path) -> None:
    """The case a string-prefix check misses, and the reason `resolve()` runs first.

    `workspace/escape/passwd` is a path *inside* the root by inspection. It is outside it in
    fact, and writing through it writes to the host. The Coder's own history has a symlink
    that leaked out of the workspace and broke every run, so this is not hypothetical.
    """
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows without developer mode; the rule is unchanged, the test cannot run
        pytest.skip("this platform does not permit creating a symlink unprivileged")

    with pytest.raises(SandboxInfrastructureError, match="outside the workspace"):
        confine(root, "escape/passwd")


def test_an_empty_path_is_refused_rather_than_resolving_to_the_root(tmp_path: Path) -> None:
    """`root / ""` is the root: it passes the containment test and is a directory.

    A caller would then try to write a file over the workspace itself and fail somewhere with
    a message about the wrong thing entirely.
    """
    for empty in ("", "   "):
        with pytest.raises(SandboxInfrastructureError, match="empty"):
            confine(tmp_path, empty)


def test_a_path_that_climbs_and_returns_is_permitted(tmp_path: Path) -> None:
    """`..` is not itself the offence — leaving the root is.

    Rejecting the substring would refuse `src/../lib/x.ts`, which lands inside the workspace
    and is a thing models write. Resolving first, then comparing, gets this right without a
    special case; a textual check gets it wrong in both directions.
    """
    assert confine(tmp_path, "src/../lib/x.ts") == (tmp_path / "lib" / "x.ts").resolve()
