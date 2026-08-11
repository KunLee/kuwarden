"""The sandbox — ADR 0005.

These run against real podman, because the properties being tested are properties of the
container runtime. A mocked sandbox proves the mock is isolated.
"""

from __future__ import annotations

import shutil

import pytest

from engine.errors import SandboxInfrastructureError
from engine.sandbox import ResourceLimits
from engine.sandbox.podman import PodmanSandbox
from engine.sandbox.workspace import changed_files, materialise, read_changes

IMAGE = "docker.io/library/python:3.11-slim"

pytestmark = pytest.mark.skipif(
    shutil.which("podman") is None, reason="podman not on PATH"
)

# Development posture: this host is rootless cgroups v1, so cgroup limits are not enforced.
# The tests assert that the sandbox *says so* rather than that it enforces them.
SANDBOX = PodmanSandbox(require_full_isolation=False)
LIMITS = ResourceLimits(memory_mb=512, cpus=1.0, pids=64, timeout_s=120, tmp_mb=64)


async def test_capabilities_are_probed_not_assumed() -> None:
    """`podman info` reports what was requested; only running a container shows what applies."""
    capabilities = await SANDBOX.capabilities()
    assert capabilities.wall_clock is True
    assert capabilities.network_isolation is True
    # Whatever the answer on this host, it must be a probed fact and the gaps must be nameable.
    assert isinstance(capabilities.fully_enforced, bool)
    if not capabilities.fully_enforced:
        assert capabilities.gaps(), "unenforced limits must be reportable"


async def test_a_host_that_cannot_enforce_limits_refuses_to_run_by_default() -> None:
    """A sandbox reporting a bound it is not applying is a control claimed but not exerted."""
    strict = PodmanSandbox(require_full_isolation=True)
    capabilities = await strict.capabilities()
    if capabilities.fully_enforced:
        pytest.skip("this host does enforce cgroup limits; nothing to refuse")

    async with materialise({"a.txt": "x"}) as workspace:
        with pytest.raises(SandboxInfrastructureError, match="does not enforce"):
            await strict.exec(workspace, IMAGE, ["true"], LIMITS)


async def test_a_command_runs_and_reports_its_exit_code() -> None:
    async with materialise({"hello.py": "print('from the sandbox')\n"}) as workspace:
        result = await SANDBOX.exec(workspace, IMAGE, ["python", "hello.py"], LIMITS)

    assert result.exit_code == 0
    assert "from the sandbox" in result.stdout
    assert result.succeeded


async def test_a_failing_command_is_a_failure_not_a_limit() -> None:
    """`exit_code` and `limits_hit` answer different questions — ADR 0005 §1."""
    async with materialise({"bad.py": "raise SystemExit(3)\n"}) as workspace:
        result = await SANDBOX.exec(workspace, IMAGE, ["python", "bad.py"], LIMITS)

    assert result.exit_code == 3
    assert result.limits_hit == []
    assert not result.succeeded


async def test_the_sandbox_has_no_network() -> None:
    """Property 2, and property 5 by construction: there is no remote to push to."""
    async with materialise({}) as workspace:
        result = await SANDBOX.exec(
            workspace,
            IMAGE,
            ["python", "-c", "import urllib.request;urllib.request.urlopen('http://example.com',timeout=5)"],
            LIMITS,
        )

    assert result.exit_code != 0
    assert "egress-denied" in result.limits_hit


async def test_a_runaway_process_is_stopped_by_the_rlimit() -> None:
    """The bound that works without cgroups, which is why it is applied as well as `--memory`."""
    async with materialise({}) as workspace:
        result = await SANDBOX.exec(
            workspace,
            IMAGE,
            ["python", "-c", "b = bytearray(900*1024*1024)"],
            ResourceLimits(memory_mb=256, timeout_s=60, tmp_mb=32),
        )

    assert result.exit_code != 0
    assert "memory" in result.limits_hit


async def test_a_hanging_command_is_killed_and_reported_as_a_timeout() -> None:
    async with materialise({}) as workspace:
        result = await SANDBOX.exec(
            workspace,
            IMAGE,
            ["sleep", "30"],
            ResourceLimits(timeout_s=5, tmp_mb=32),
        )

    assert "timeout" in result.limits_hit
    assert not result.succeeded


async def test_edits_survive_between_commands_but_containers_do_not() -> None:
    """The split that makes the inner loop work: fresh container, persistent workspace."""
    async with materialise({"counter.txt": "0\n"}) as workspace:
        await SANDBOX.exec(workspace, IMAGE, ["sh", "-c", "echo 1 > counter.txt"], LIMITS)
        second = await SANDBOX.exec(workspace, IMAGE, ["cat", "counter.txt"], LIMITS)

    assert second.stdout.strip() == "1"


async def test_the_diff_comes_from_git_not_from_the_agent() -> None:
    """An agent's account of what it changed is never a gate input."""
    async with materialise({"src.py": "old\n"}) as workspace:
        await SANDBOX.exec(
            workspace,
            IMAGE,
            ["sh", "-c", "echo new > src.py; echo added > extra.py"],
            LIMITS,
        )
        changes = await changed_files(workspace)
        contents = await read_changes(workspace)

    paths = {change.path for change in changes}
    assert paths == {"src.py", "extra.py"}
    assert contents["extra.py"].strip() == "added"


async def test_the_workspace_is_destroyed_on_exit() -> None:
    """Property 3: one run cannot poison the next."""
    from pathlib import Path

    async with materialise({"a.txt": "x"}) as workspace:
        root = Path(workspace.root)
        assert root.is_dir()
    assert not root.exists()
