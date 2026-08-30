"""What the Coder shows the model, and how it applies what comes back.

Both are pure functions over a directory, so these run without podman or Temporal — which
matters, because the bug they cover was invisible on every host that skipped the sandbox
suite and only appeared as a failure three nodes downstream in production.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from engine.errors import SandboxInfrastructureError
from engine.nodes import repo_context
from engine.nodes.coder import _apply, _prompt
from engine.sandbox import Workspace
from engine.state import ChangePlan, FlowState, Ticket


def _state() -> FlowState:
    return FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="t", body="."),
        policy_commit="0" * 40,
        policy_bundle={},
        plan=ChangePlan(summary="do the thing", steps=["one"]),
    )


def test_every_text_file_reaches_the_model_however_many_there_are() -> None:
    """No cap, and the count in the record is the whole repository.

    There is no retrieval step, so a cap never chose *less* context — it chose an arbitrary
    alphabetical prefix. Ticket 35 asked for a fix to `components/Header.tsx` in an 88-file
    repository and the model was sent `app/admin/` instead, because `app` sorts first. It had
    only the filename to go on, correctly refused to invent the contents, and the run died at
    Push with a message naming neither the file nor the reason.
    """
    body = ("x = 1\n" * 400).encode()
    files = {f"src/module_{i:03}.py": body for i in range(60)}
    cacheable, tail, assembly = _prompt(_state(), files, None)
    # Split for prompt caching, joined here: what the model saw is both halves.
    prompt = cacheable + "\n\n" + tail

    assert assembly["listed"] == 60
    assert assembly["shown"] == 60, "every text file is sent, whatever the total size"
    assert assembly["omitted"] == 0
    for path in files:
        assert f"<file path='{path}'>" in prompt, f"{path} was listed but not inlined"


def test_binary_files_are_still_listed_rather_than_inlined() -> None:
    """One of the two remaining reasons contents are withheld, and it is about content.

    Binary bytes are noise to a model and do not decode. Unlike the old byte budget, this
    cannot silently swallow the source file a ticket names.
    """
    files = {"logo.png": b"\x89PNG\r\n\x00\x1a\n" + b"\x00" * 64, "app.py": b"x = 1\n"}
    cacheable, tail, assembly = _prompt(_state(), files, None)
    # Split for prompt caching, joined here: what the model saw is both halves.
    prompt = cacheable + "\n\n" + tail

    assert assembly["shown"] == 1
    assert assembly["omitted"] == 1
    assert "logo.png" in prompt, "a binary file is still named in the listing"
    assert "<file path='logo.png'>" not in prompt
    assert "binary or machine-generated" in prompt


def test_the_model_can_delete_a_file(tmp_path: Path) -> None:
    """`deleted` removes the path instead of writing it.

    Without this the model had no way to express a removal at all: `EDIT_SCHEMA` carried only
    `path` and `content`, so the closest it could get was rewriting a file to the empty
    string — which is a different change, and leaves every import of it still resolving.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dead.py").write_text("# to be removed\n", encoding="utf-8")
    (tmp_path / "src" / "kept.py").write_text("x = 1\n", encoding="utf-8")

    written = _apply(
        Workspace(root=str(tmp_path)),
        {
            "edits": [
                {"path": "src/dead.py", "content": "", "deleted": True},
                {"path": "src/kept.py", "content": "x = 2\n", "deleted": False},
            ]
        },
    )

    assert sorted(written) == ["src/dead.py", "src/kept.py"]
    assert not (tmp_path / "src" / "dead.py").exists(), "deleted, not emptied"
    assert (tmp_path / "src" / "kept.py").read_text(encoding="utf-8") == "x = 2\n"


def test_deleting_a_path_that_is_already_gone_is_not_a_failure(tmp_path: Path) -> None:
    """A later attempt may repeat an earlier attempt's delete. git decides what changed."""
    written = _apply(
        Workspace(root=str(tmp_path)),
        {"edits": [{"path": "src/never.py", "content": "", "deleted": True}]},
    )
    assert written == ["src/never.py"]


def test_a_deletion_may_not_escape_the_workspace(tmp_path: Path) -> None:
    """The path check runs before the branch that unlinks, not only before the one that writes.

    The model supplies these strings, so `../../.ssh/authorized_keys` is exactly what a
    successful prompt injection produces — and a delete verb that skipped the check would be a
    way to remove host files rather than merely write them.
    """
    with pytest.raises(SandboxInfrastructureError):
        _apply(
            Workspace(root=str(tmp_path)),
            {"edits": [{"path": "../../escape.txt", "content": "", "deleted": True}]},
        )


def test_a_lockfile_is_listed_but_never_inlined() -> None:
    """Generated files are withheld by name, and the record says so.

    Not a size cap wearing a disguise: the rule is by exact filename, so it is deterministic
    and checkable, and the file is still named in the listing. In sasagayo `package-lock.json`
    is 426 KB against 300 KB for the whole hand-written codebase — 59% of every prompt, four
    times a run, describing a dependency graph no ticket asks to change.
    """
    files = {
        "package.json": b'{"name": "app"}\n',
        "package-lock.json": b'{"lockfileVersion": 3}\n' + b'{"x":1}\n' * 5000,
        "src/app.ts": b"export const x = 1;\n",
    }
    cacheable, tail, assembly = _prompt(_state(), files, None)
    # Split for prompt caching, joined here: what the model saw is both halves.
    prompt = cacheable + "\n\n" + tail

    assert assembly["shown"] == 2
    assert assembly["omitted"] == 1
    assert "package-lock.json" in prompt, "still named, so the model knows it exists"
    assert "<file path='package-lock.json'>" not in prompt
    # package.json is hand-written and stays: a dependency the ticket names is readable there.
    assert "<file path='package.json'>" in prompt
    # The instruction lives in the Coder's own note rather than the shared renderer: the
    # verifiers see the same repository and have nothing to edit, so it would be noise.
    assert "Do not edit a lockfile" in prompt


def test_the_listing_stays_complete_even_when_contents_are_selective() -> None:
    """The safety property the whole selection design rests on.

    A model that is shown a partial repository and told nothing concludes the missing files do
    not exist. That produced both of the worst bugs here: a Coder that could not see
    `components/Header.tsx` on a ticket about `components/Header.tsx`, and verifiers that
    blocked a valid change because `globals.css` "was not among the changed files, so there is
    no evidence the ocean tokens exist".

    So contents may be selective; the listing may never be. Every path is always visible, and
    the note says in as many words that more can be requested.
    """
    files = {
        "components/Header.tsx": b"export const Header = 1;\n",
        "app/globals.css": b'[data-theme="ocean"] { --bg: #123; }\n',
        "app/admin/AdminClient.tsx": b"export const Admin = 1;\n",
    }
    prompt, assembly = repo_context.render(files, "repository", ["components/Header.tsx"])

    assert assembly["shown"] == 1
    assert assembly["withheld"] == 2
    # Named, all three of them.
    for path in files:
        assert path in prompt, f"{path} must always be listed"
    assert "<file path='components/Header.tsx'>" in prompt
    assert "<file path='app/admin/AdminClient.tsx'>" not in prompt
    assert "say so in `reasoning` if you need one" in prompt


def test_imports_are_followed_so_a_selection_need_not_be_exhaustive() -> None:
    """Asking for a component must bring what that component depends on.

    Otherwise every selection has to be perfect, and an imperfect one costs an attempt. The
    model names the file it is editing; the closure supplies the neighbourhood it edits
    against — the alias import Next.js projects use, and relative siblings.
    """
    files = {
        "components/Header.tsx": (
            b'import SearchPalette from "@/components/SearchPalette";\n'
            b'import { nav } from "./nav";\n'
            b'import React from "react";\n'
        ),
        "components/SearchPalette.tsx": b'import { q } from "@/lib/site";\n',
        "components/nav.ts": b"export const nav = [];\n",
        "lib/site.js": b"export const q = 1;\n",
        "app/admin/AdminClient.tsx": b"export const Admin = 1;\n",
    }
    chosen = repo_context.closure(files, ["components/Header.tsx"])

    assert chosen == {
        "components/Header.tsx",
        "components/SearchPalette.tsx",
        "components/nav.ts",
        "lib/site.js",
    }
    # `react` is a dependency, not a file of theirs, and resolves to nothing.
    assert not any(c.startswith("node_modules") for c in chosen)
    assert "app/admin/AdminClient.tsx" not in chosen, "unrelated code is not dragged in"


def test_the_callers_of_a_changed_file_are_found() -> None:
    """`regression_risk` asks what else breaks, and the answer is only in the callers.

    A changed prop, a changed return type or a removed export is invisible in the file that
    changed and obvious in the file that uses it. Following imports forwards cannot reach
    them: `Header` does not import the page that renders it. So the index is built backwards
    as well, and it is the only way that verifier can answer its own question rather than
    reporting that it could not see enough — which reads as a finding and is a context
    failure.
    """
    files = {
        "components/Header.tsx": b'import { u } from "@/lib/utils";\n',
        "app/page.tsx": b'import Header from "@/components/Header";\n',
        "app/about/page.tsx": b'import Header from "../../components/Header";\n',
        "lib/utils.js": b"export const u = 1;\n",
        "app/admin/page.tsx": b"export default function Admin() {}\n",
    }

    callers = repo_context.dependents(files, ["components/Header.tsx"])

    assert callers == {"app/page.tsx", "app/about/page.tsx"}, (
        "both the alias import and the relative one resolve to the same file"
    )
    # Forward and backward are different sets, and a reviewer needs both.
    forward = repo_context.closure(files, ["components/Header.tsx"])
    assert forward == {"components/Header.tsx", "lib/utils.js"}
    assert "app/admin/page.tsx" not in callers | forward
