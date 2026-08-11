"""Building the workspace, and reading the diff back out of it.

Both happen **outside** the sandbox, in the Flow Engine, which is the half that holds
credentials. The sandbox is handed a directory that already contains the code and never
learns where it came from.

The baseline commit is the mechanism for property 5. A local git repository with no remote
gives us `git diff` for free and gives the model familiar tooling, while there is nothing to
push to even if it tried.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from engine.errors import SandboxInfrastructureError
from engine.sandbox import FileChange, RepoPin, Workspace

BASELINE_MESSAGE = "kuwarden-baseline"


@asynccontextmanager
async def materialise(
    files: Mapping[str, str | bytes],
    repos: list[RepoPin] | None = None,
) -> AsyncIterator[Workspace]:
    """Write files to a temporary directory, commit a baseline, yield it, then destroy it.

    `files` comes from the SCM adapter, which holds the token. Nothing about that credential
    reaches the directory — only file contents do.

    Property 3: the directory is removed on exit whatever happened, so one run cannot poison
    the next.
    """
    root = Path(tempfile.mkdtemp(prefix="kuwarden-ws-"))
    try:
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, because a repository contains binaries and encoding everything as UTF-8
            # corrupts them silently -- an image that still opens is worse than one that
            # fails to write.
            target.write_bytes(
                content.encode("utf-8") if isinstance(content, str) else content
            )

        await _git(root, "init", "--quiet", "--initial-branch=kuwarden")
        # Identity is local to this throwaway repository; there is no remote and no signing.
        await _git(root, "config", "user.email", "sandbox@kuwarden.invalid")
        await _git(root, "config", "user.name", "KuWarden Sandbox")
        await _git(root, "add", "-A")
        await _git(root, "commit", "--quiet", "--allow-empty", "-m", BASELINE_MESSAGE)

        yield Workspace(root=str(root), repos=repos or [])
    finally:
        _destroy(root)


def _destroy(root: Path) -> None:
    """Remove the workspace, and fail loudly if it survives.

    `ignore_errors=True` is wrong here and was the original bug: git writes its object files
    read-only, Windows refuses to unlink a read-only file, and the flag turned a failed
    deletion into a silent one. Property 3 then reads as satisfied while a tree containing
    the customer's source code stays on disk indefinitely.

    A security property that fails quietly is worse than one that is absent, because nobody
    goes looking for it.
    """

    def clear_readonly(func: Any, path: str, exc: BaseException) -> None:
        Path(path).chmod(stat.S_IWRITE)
        func(path)

    shutil.rmtree(root, onexc=clear_readonly)
    if root.exists():
        raise SandboxInfrastructureError(
            f"workspace {root} still exists after cleanup; it holds source code and must not "
            "outlive the run"
        )


async def changed_files(workspace: Workspace) -> list[FileChange]:
    """What the Coder actually changed, against the baseline commit.

    This is the input to the `protected_paths` check. It is computed from git rather than
    from the model's own account of what it edited — an agent's claim about its own output is
    never a gate input.
    """
    root = Path(workspace.root)
    await _git(root, "add", "-A")
    output = await _git(root, "diff", "--numstat", "--cached", "HEAD")

    changes: list[FileChange] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        changes.append(
            FileChange(
                path=path,
                # Binary files report "-" for both counts.
                added=int(added) if added.isdigit() else 0,
                removed=int(removed) if removed.isdigit() else 0,
            )
        )
    return changes


async def read_changes(workspace: Workspace) -> dict[str, str]:
    """The content of every changed file, for the Flow Engine to push.

    The sandbox produces the change; something outside it, under a different identity, sends
    it onward — ADR 0005 property 5.
    """
    root = Path(workspace.root)
    contents: dict[str, str] = {}
    for change in await changed_files(workspace):
        path = root / change.path
        if path.is_file():
            contents[change.path] = path.read_text(encoding="utf-8", errors="replace")
    return contents


async def _git(root: Path, *args: str) -> str:
    """Run git in the workspace. Never touches a network — there is no remote configured."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(root),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise SandboxInfrastructureError(
            f"git {' '.join(args)} failed: {stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace")
