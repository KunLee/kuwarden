"""The Coder's inner loop, against a real sandbox running real tests.

`pytest` genuinely runs inside the container here. A mocked sandbox would prove the mock
loops; what needs proving is that a failing test comes back as a failure the next attempt can
read, which is the entire mechanism ADR 0002 replaced the linear pipeline to get.

Requires the stack and the toolchain image:
    uv run python -m engine.sandbox build
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from engine.activities.nodes import RUNTIME
from engine.errors import SandboxInfrastructureError
from engine.nodes import NODES
from engine.nodes.base import bound
from engine.state import ChangePlan, FlowState, Ticket
from tests.conftest import FakePlatform

pytestmark = pytest.mark.skipif(shutil.which("podman") is None, reason="podman not on PATH")

# BROKEN fails the repository's existing test. FIXED passes it *and* differs from the
# original file, so there is a real diff at the end.
#
# An earlier version used a FIXED byte-identical to the original and got an empty diff —
# correctly, because git reports the net change and the model had simply restored what was
# already there. Worth keeping in mind when reading a run: "the model edited twice" and "the
# diff is non-empty" are different claims.
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = '"""Arithmetic helpers."""\n\n\ndef add(a, b):\n    return a + b\n'


def _state() -> FlowState:
    return FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="change add", body="."),
        policy_commit="0" * 40,
        policy_bundle={},
        plan=ChangePlan(summary="adjust add", steps=["edit src/app.py"]),
    )


async def _run_coder(platform: FakePlatform) -> FlowState:
    with bound(RUNTIME.context()):
        return await NODES["coder"](_state())


async def test_the_coder_edits_real_code_and_the_diff_holds_only_what_changed(
    real_sandbox_platform: FakePlatform,
) -> None:
    """The tree is pulled from the SCM adapter; the diff is computed by git afterwards."""
    real_sandbox_platform.coder_edits = [
        {"path": "src/app.py", "content": FIXED + "\n\ndef sub(a, b):\n    return a - b\n"}
    ]

    state = await _run_coder(real_sandbox_platform)

    changed = {edit.path for edit in state.proposed_edits}
    assert changed == {"src/app.py"}, "untouched files must not appear in the diff"
    assert "def sub" in state.proposed_edits[0].content

    # The repository was genuinely read, not invented.
    assert any("/git/trees/" in r.url.path for r in real_sandbox_platform.requests)


async def test_a_failing_test_drives_another_attempt(
    real_sandbox_platform: FakePlatform,
) -> None:
    """The feedback edge. Retrying without the failure is repeated guessing — ADR 0002."""
    attempts: list[int] = []

    def edits_for_attempt() -> list[dict[str, str]]:
        attempts.append(1)
        # Break it first, so pytest fails and the loop must come back for a second try.
        return [{"path": "src/app.py", "content": BROKEN if len(attempts) == 1 else FIXED}]

    real_sandbox_platform.coder_edits_factory = edits_for_attempt

    state = await _run_coder(real_sandbox_platform)

    assert len(attempts) >= 2, "a failing suite must produce another attempt"
    assert state.retry_count >= 1
    assert state.proposed_edits[0].content == FIXED


async def test_the_failure_output_reaches_the_next_prompt(
    real_sandbox_platform: FakePlatform,
) -> None:
    """Without the test output, the second attempt is the first attempt again."""
    seen: list[str] = []

    def edits_for_attempt() -> list[dict[str, str]]:
        seen.append(str(real_sandbox_platform.messages_requests[-1]))
        return [{"path": "src/app.py", "content": BROKEN if len(seen) == 1 else FIXED}]

    real_sandbox_platform.coder_edits_factory = edits_for_attempt
    await _run_coder(real_sandbox_platform)

    assert len(seen) >= 2
    assert "previous_attempt" in seen[1], "the second prompt must carry the failure"
    assert "assert" in seen[1].lower(), "and the actual pytest output, not just a flag"


async def test_the_loop_stops_at_the_retry_budget(
    real_sandbox_platform: FakePlatform,
) -> None:
    """Bounded, because an unbounded loop is an unbounded bill."""
    attempts: list[int] = []

    def never_fixes() -> list[dict[str, str]]:
        attempts.append(1)
        return [{"path": "src/app.py", "content": BROKEN}]

    real_sandbox_platform.coder_edits_factory = never_fixes
    state = await _run_coder(real_sandbox_platform)

    # max_coder_retries defaults to 3, so four attempts in total.
    assert len(attempts) == 4
    assert state.retry_count == 3


async def test_a_path_escaping_the_workspace_is_refused(
    real_sandbox_platform: FakePlatform,
) -> None:
    """Exactly what a successful prompt injection produces, and the write happens host-side."""
    real_sandbox_platform.coder_edits = [{"path": "../../escaped.txt", "content": "owned"}]

    with pytest.raises(SandboxInfrastructureError, match="outside the workspace"):
        await _run_coder(real_sandbox_platform)


async def test_binary_files_are_listed_but_not_inlined(
    real_sandbox_platform: FakePlatform,
) -> None:
    """Their bytes are noise to a model and would exhaust the budget source code needs."""
    real_sandbox_platform.repo_files["assets/logo.png"] = "\x00\x01binary\x00"
    real_sandbox_platform.coder_edits = [{"path": "src/app.py", "content": FIXED}]

    await _run_coder(real_sandbox_platform)

    prompt = str(real_sandbox_platform.messages_requests[-1])
    assert "assets/logo.png" in prompt, "it must still appear in the listing"
    assert "binary or machine-generated" in prompt, "and the model is told why"
