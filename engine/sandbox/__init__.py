"""The execution sandbox contract — ADR 0005.

Where the `Coder ⇄ Build & Test` cycle actually runs. The code executed here was written by a
model that just read a ticket anyone can file, so this is the boundary that decides whether a
successful prompt injection is a nuisance or an incident.

Five properties, none optional:

1. No credentials inside. Ever.
2. No egress except an allowlisted package mirror.
3. Ephemeral filesystem, destroyed on completion.
4. Resource and wall-clock limits, always.
5. Produces a diff. Never pushes.

Property 5 is not enforced by policy here — with no network the sandbox cannot reach a remote
at all, so "never pushes" is a physical fact rather than a rule someone has to remember.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ResourceLimits:
    """Bounds on one execution.

    `memory_mb` is per process where cgroups are unavailable — see `SandboxCapabilities`. An
    agent loop will otherwise consume the machine it runs on.
    """

    memory_mb: int = 2048
    cpus: float = 2.0
    pids: int = 256
    timeout_s: int = 600
    #: Writable scratch outside the workspace. Bounded so a runaway build cannot fill the host.
    tmp_mb: int = 512


@dataclass(frozen=True)
class SandboxCapabilities:
    """What the host platform actually enforces, as probed rather than assumed.

    This exists because of a real failure mode: podman on a rootless cgroups v1 host accepts
    `--memory` and silently ignores it. A sandbox that reports a limit it is not applying is
    the same class of error as an audit row claiming `authorized` for something merely
    observed (invariant 11) — a control claimed but not exerted. `ExecResult` therefore
    carries what was enforced, and `require_full_isolation` lets a deployment refuse to run
    at all rather than run pretending.
    """

    #: cgroup-backed total memory across every process in the container.
    cgroup_memory: bool
    #: cgroup-backed CPU quota.
    cgroup_cpu: bool
    #: cgroup-backed process-count cap.
    cgroup_pids: bool
    #: Per-process address-space cap via rlimit. Works without cgroups.
    rlimit_memory: bool
    #: tmpfs `size=`, enforced by the kernel mount rather than by cgroups.
    tmpfs_quota: bool
    #: We own the container process, so this never depends on the platform.
    wall_clock: bool = True
    #: Network namespace isolation — the one that makes property 5 physical.
    network_isolation: bool = True

    @property
    def fully_enforced(self) -> bool:
        """True when every ADR 0005 property 4 bound is actually applied."""
        return self.cgroup_memory and self.cgroup_cpu and self.cgroup_pids

    def gaps(self) -> list[str]:
        """Human-readable list of what is *not* enforced. Empty when nothing is missing."""
        missing = []
        if not self.cgroup_memory:
            missing.append(
                "container-total memory (cgroups unavailable; per-process rlimit only, so "
                "N processes at the limit can still exhaust the host)"
            )
        if not self.cgroup_cpu:
            missing.append("CPU quota (cgroups unavailable)")
        if not self.cgroup_pids:
            missing.append("process-count cap (cgroups unavailable)")
        return missing


@dataclass(frozen=True)
class RepoPin:
    """One repository at one commit."""

    name: str
    path: str
    commit: str


@dataclass(frozen=True)
class Workspace:
    """One or more repositories the Coder sees as a single tree — ADR 0005 §2.

    A repository is not the unit. Contract-coupled changes across services are authored in
    one context, because splitting them produces interface drift that each side's tests pass.
    """

    root: str
    repos: list[RepoPin] = field(default_factory=list)


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    removed: int


@dataclass(frozen=True)
class ExecResult:
    """The outcome of one command.

    `limits_hit` is separate from `exit_code` deliberately: "the tests failed" and "we killed
    it at 600 seconds" are different facts, and the Coder must tell them apart to decide
    whether retrying is even sensible — ADR 0005 §1.
    """

    #: The reality anchor. Nothing else is.
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    #: "timeout" | "memory" | "disk" | "egress-denied"
    limits_hit: list[str] = field(default_factory=list)
    changed_files: list[FileChange] = field(default_factory=list)
    #: What the platform actually applied for this execution. Never assumed.
    enforced: SandboxCapabilities | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.limits_hit


class Sandbox(Protocol):
    """Run a command against a workspace, in isolation."""

    async def capabilities(self) -> SandboxCapabilities:
        """What this host actually enforces. Probed once, then cached."""
        ...

    async def exec(
        self,
        workspace: Workspace,
        toolchain_id: str,
        command: list[str],
        limits: ResourceLimits,
    ) -> ExecResult: ...
