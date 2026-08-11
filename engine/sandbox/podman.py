"""Podman-backed sandbox.

One container per command, destroyed immediately; the workspace directory outlives it and is
mounted in. That split is what makes the inner loop possible — the Coder edits a file in one
call and runs the tests in the next, over the same tree, without any container accumulating
state between them.

`--network=none` is doing more work than it appears to. It provides property 2 (no egress)
and it makes property 5 (never pushes) physical rather than procedural: with no network there
is no remote to push to, whatever the model decides to try.

**Dependencies are baked into the toolchain image.** With no egress there is no `pip install`
at runtime, and ADR 0005 §4's "cold install each run" is not achievable without the egress
proxy that section also defers. Stated here because it is the first thing anyone hits.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from pathlib import Path

from engine.errors import SandboxInfrastructureError
from engine.sandbox import (
    ExecResult,
    ResourceLimits,
    SandboxCapabilities,
    Workspace,
)

#: Probing runs a container, so the result is cached for the process lifetime. Capabilities
#: are a property of the host, and the host does not change under a running worker.
_CAPABILITIES: SandboxCapabilities | None = None

PROBE_IMAGE = "docker.io/library/python:3.11-slim"

log = logging.getLogger(__name__)


class PodmanSandbox:
    """Implements `Sandbox` using rootless or rootful podman.

    `require_full_isolation` is the important knob. True refuses to run on a host that cannot
    enforce cgroup limits; False runs anyway and logs the degradation on every execution.

    This class defaults to True — strict unless told otherwise. `SandboxConfig` currently
    defaults to False for the testing phase, so the relaxation is a visible line in
    `kuwarden.yaml` rather than a property of the code.
    """

    def __init__(
        self,
        *,
        binary: str = "podman",
        require_full_isolation: bool = True,
    ) -> None:
        self._binary = binary
        self._require_full_isolation = require_full_isolation

    async def capabilities(self) -> SandboxCapabilities:
        """Probe what the host enforces, by testing it rather than asking it.

        `podman info` reports what was *requested*; only running a container reveals what is
        applied. On a rootless cgroups v1 host, `--memory` is accepted and ignored with a
        warning on stderr that is easy to miss and easier to parse wrongly.
        """
        global _CAPABILITIES
        if _CAPABILITIES is not None:
            return _CAPABILITIES

        # Ask for 64 MiB, then try to allocate 128 MiB. Success means the limit is not real.
        allocate = "python -c \"b=bytearray(128*1024*1024); print('ALLOCATED')\""
        code, out, err = await self._run(
            [
                self._binary, "run", "--rm", "--network=none",
                "--memory=64m", "--memory-swap=64m",
                PROBE_IMAGE, "sh", "-lc", allocate,
            ],
            timeout_s=120,
        )
        cgroups_work = "ALLOCATED" not in out

        _CAPABILITIES = SandboxCapabilities(
            cgroup_memory=cgroups_work,
            # podman applies all three through the same cgroup path, so one probe settles it.
            cgroup_cpu=cgroups_work,
            cgroup_pids=cgroups_work,
            rlimit_memory=True,
            tmpfs_quota=True,
        )
        return _CAPABILITIES

    async def exec(
        self,
        workspace: Workspace,
        toolchain_id: str,
        command: list[str],
        limits: ResourceLimits,
    ) -> ExecResult:
        capabilities = await self.capabilities()
        if not capabilities.fully_enforced:
            gaps = "; ".join(capabilities.gaps())
            if self._require_full_isolation:
                raise SandboxInfrastructureError(
                    f"this host does not enforce: {gaps}. Use a rootful podman machine or a "
                    "cgroups v2 host, or set sandbox.require_full_isolation: false knowingly."
                )
            # Logged on every execution rather than once at startup. A warning printed at
            # boot scrolls away; one attached to each execution is still there when someone
            # asks why a run behaved oddly.
            log.warning("sandbox running with weakened isolation: %s", gaps)

        root = Path(workspace.root)
        if not root.is_dir():
            raise SandboxInfrastructureError(f"workspace {root} does not exist")

        # rlimits are applied inside the shell because they work where cgroups do not: an
        # rlimit is per-process and needs no controller. It bounds one runaway process, not
        # the container's total, which is why `capabilities` reports the difference.
        guarded = "; ".join(
            [
                f"ulimit -v {limits.memory_mb * 1024}",
                f"ulimit -u {limits.pids}",
                shlex.join(command),
            ]
        )

        argv = [
            self._binary, "run", "--rm",
            # Property 2, and property 5 by construction.
            "--network=none",
            # Property 3: nothing but the mounted workspace survives, and root is read-only.
            "--read-only",
            f"--tmpfs=/tmp:size={limits.tmp_mb}m,exec",
            # Property 4, where the host allows it.
            f"--memory={limits.memory_mb}m",
            f"--cpus={limits.cpus}",
            f"--pids-limit={limits.pids}",
            # Least privilege. The container never needs to add capabilities or escalate.
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            # Property 1. podman does not forward the host environment unless asked, so no
            # token leaks in by default — this only gives the process a writable HOME, since
            # the root filesystem is read-only.
            "--env=HOME=/tmp",
            f"--volume={root}:/workspace:Z",
            "--workdir=/workspace",
            toolchain_id,
            "sh", "-lc", guarded,
        ]

        started = time.monotonic()
        code, out, err = await self._run(argv, timeout_s=limits.timeout_s)
        duration_ms = int((time.monotonic() - started) * 1000)

        return ExecResult(
            exit_code=code,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
            limits_hit=_limits_hit(code, err, duration_ms, limits),
            enforced=capabilities,
        )

    async def _run(self, argv: list[str], *, timeout_s: int) -> tuple[int, str, str]:
        """Run podman, killing it on timeout.

        The wall clock is ours: we own this process, so this bound holds regardless of what
        the host's cgroup support is. Return code 124 mirrors `timeout(1)`.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise SandboxInfrastructureError(f"{self._binary} not found on PATH") from None

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            return 124, "", f"killed after {timeout_s}s"

        return (
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )


def _limits_hit(code: int, stderr: str, duration_ms: int, limits: ResourceLimits) -> list[str]:
    """Which bound was reached, if any.

    Distinguished from a non-zero exit because the Coder needs to know whether the code is
    wrong or the environment was too small — retrying is sensible for one and not the other.
    """
    hit: list[str] = []
    if code == 124 or duration_ms >= limits.timeout_s * 1000:
        hit.append("timeout")
    # 137 is SIGKILL, which is how the OOM killer and a cgroup memory limit both present.
    if code == 137 or "MemoryError" in stderr or "Out of memory" in stderr:
        hit.append("memory")
    if "No space left on device" in stderr:
        hit.append("disk")
    if "Temporary failure in name resolution" in stderr or "Network is unreachable" in stderr:
        # Expected, and worth surfacing: it means the command tried to reach the network.
        hit.append("egress-denied")
    return hit
