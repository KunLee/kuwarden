"""Reading a repository tree — the input the sandbox workspace is built from.

Most of these assert that a bound is **refused** rather than quietly applied. A Coder editing
a silently truncated tree produces a change against a repository that does not exist, and the
result reads as a plausible mistake rather than as a missing file.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from engine.adapters.credentials import EnvCredentialBroker
from engine.adapters.protocols import RepoRef, TreeLimits
from engine.adapters.scm.github import GitHubScm
from engine.adapters.scm.tree import TreeTooLarge, excluded

BROKER = EnvCredentialBroker({"KUWARDEN_SCM_TOKEN": "gh-t"})
REPO = RepoRef(host="github.com", org="acme", repo="payments-service")


def _github(tree: dict[str, object], blobs: dict[str, str]) -> GitHubScm:
    """Serve one tree listing and a blob per sha."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/git/trees/" in path:
            return httpx.Response(200, json=tree)
        if "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[-1]
            content = blobs.get(sha, "")
            return httpx.Response(
                200, json={"content": base64.b64encode(content.encode()).decode()}
            )
        return httpx.Response(404, text=path)

    return GitHubScm(BROKER, transport=httpx.MockTransport(handler))


def _blob(path: str, sha: str, size: int = 10) -> dict[str, object]:
    return {"type": "blob", "path": path, "sha": sha, "size": size}


async def test_a_tree_becomes_path_to_bytes() -> None:
    scm = _github(
        {"truncated": False, "tree": [_blob("src/app.py", "a"), _blob("README.md", "b")]},
        {"a": "print('hi')", "b": "# docs"},
    )
    tree = await scm.read_tree(REPO, "c0ffee")

    assert set(tree.files) == {"src/app.py", "README.md"}
    assert tree.files["src/app.py"] == b"print('hi')"
    assert tree.commit == "c0ffee"


async def test_a_truncated_tree_is_refused() -> None:
    """GitHub truncates large trees silently. Accepting that hands the Coder a repository
    missing arbitrary files, with nothing to say which."""
    scm = _github({"truncated": True, "tree": [_blob("src/app.py", "a")]}, {"a": "x"})

    with pytest.raises(TreeTooLarge, match="truncated"):
        await scm.read_tree(REPO, "c0ffee")


async def test_too_many_files_is_refused_not_trimmed() -> None:
    listing = {
        "truncated": False,
        "tree": [_blob(f"src/f{i}.py", str(i)) for i in range(50)],
    }
    scm = _github(listing, {str(i): "x" for i in range(50)})

    with pytest.raises(TreeTooLarge, match="over the 10 limit"):
        await scm.read_tree(REPO, "c0ffee", TreeLimits(max_files=10))


async def test_an_oversized_file_is_refused() -> None:
    scm = _github(
        {"truncated": False, "tree": [_blob("big.bin", "a", size=5_000_000)]}, {"a": "x"}
    )
    with pytest.raises(TreeTooLarge, match="per-file limit"):
        await scm.read_tree(REPO, "c0ffee", TreeLimits(max_file_bytes=1_000))


async def test_the_total_size_is_checked_after_download() -> None:
    """Reported sizes can be wrong or absent; the total is checked against what arrived."""
    listing = {"truncated": False, "tree": [_blob("a.txt", "a"), _blob("b.txt", "b")]}
    scm = _github(listing, {"a": "x" * 800, "b": "y" * 800})

    with pytest.raises(TreeTooLarge, match="total limit"):
        await scm.read_tree(REPO, "c0ffee", TreeLimits(max_total_bytes=1_000))


async def test_excluded_paths_are_never_downloaded() -> None:
    """Vendored dependencies are large, are not what anyone asked the agent to change, and
    would dominate the model's context."""
    listing = {
        "truncated": False,
        "tree": [
            _blob("src/app.py", "a"),
            _blob("node_modules/left-pad/index.js", "b"),
            _blob(".git/config", "c"),
            _blob("__pycache__/app.cpython-312.pyc", "d"),
        ],
    }
    scm = _github(listing, {"a": "code", "b": "vendor", "c": "gitcfg", "d": "bytecode"})
    tree = await scm.read_tree(REPO, "c0ffee")

    assert set(tree.files) == {"src/app.py"}


@pytest.mark.parametrize(
    "path",
    ["node_modules/x/index.js", ".git/HEAD", "src/__pycache__/m.pyc", "dist/bundle.js"],
)
def test_exclusions_match(path: str) -> None:
    assert excluded(path, TreeLimits()) is True


@pytest.mark.parametrize("path", ["src/app.py", "tests/test_app.py", "README.md"])
def test_ordinary_source_is_not_excluded(path: str) -> None:
    assert excluded(path, TreeLimits()) is False


async def test_binary_content_survives_the_round_trip() -> None:
    """Bytes rather than text: decoding everything as UTF-8 turns a PNG into a crash three
    nodes later."""
    png = b"\x89PNG\r\n\x1a\n\x00\x01\x02"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in request.url.path:
            return httpx.Response(
                200, json={"truncated": False, "tree": [_blob("logo.png", "a")]}
            )
        return httpx.Response(200, json={"content": base64.b64encode(png).decode()})

    scm = GitHubScm(BROKER, transport=httpx.MockTransport(handler))
    tree = await scm.read_tree(REPO, "c0ffee")

    assert tree.files["logo.png"] == png
