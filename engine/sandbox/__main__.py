"""`uv run python -m engine.sandbox doctor|build`.

The sandbox is the one component whose correctness depends on the *host*, not on our code.
`doctor` answers "is this machine actually capable of running it" in a way that names the
gap and the fix, rather than leaving someone to discover mid-run that `--memory` was being
ignored all along.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from engine.config import SandboxConfig
from engine.sandbox import ResourceLimits
from engine.sandbox.podman import PodmanSandbox
from engine.sandbox.workspace import materialise

TOOLCHAINS = Path(__file__).parent / "toolchains"


async def doctor() -> int:
    """Report what this host enforces, and what to do about anything it does not."""
    sandbox = PodmanSandbox(require_full_isolation=False)

    print("Probing the host (this runs a container, ~5s)…\n")
    try:
        capabilities = await sandbox.capabilities()
    except Exception as exc:
        print(f"  [!!] could not probe: {exc}")
        return 1

    rows = [
        ("wall-clock timeout", capabilities.wall_clock, "we own the process"),
        ("network isolation", capabilities.network_isolation, "--network=none"),
        ("per-process memory", capabilities.rlimit_memory, "ulimit -v"),
        ("scratch disk quota", capabilities.tmpfs_quota, "tmpfs size="),
        ("container memory", capabilities.cgroup_memory, "cgroups"),
        ("CPU quota", capabilities.cgroup_cpu, "cgroups"),
        ("process-count cap", capabilities.cgroup_pids, "cgroups"),
    ]
    for label, ok, how in rows:
        mark = "[ok]" if ok else "[--]"
        state = "enforced" if ok else "NOT enforced"
        print(f"  {mark} {label:22} {state:14} ({how})")

    print()
    if capabilities.fully_enforced:
        print("  All ADR 0005 property 4 bounds are enforced. require_full_isolation=true is safe.")
        return 0

    print("  Gaps:")
    for gap in capabilities.gaps():
        print(f"    - {gap}")
    print(
        "\n  Cause: rootless podman on a cgroups v1 host accepts --memory/--cpus/--pids-limit\n"
        "  and silently ignores them. Rootless is the problem here, not rootful.\n"
        "\n  Fixes, strongest first:\n"
        "    1. A Linux host with cgroups v2 (most modern distributions).\n"
        "    2. podman machine set --rootful   <- note: rootFUL, and it uses SEPARATE storage,\n"
        "       so existing containers and volumes need recreating.\n"
        "    3. sandbox.require_full_isolation: false in kuwarden.yaml - development only.\n"
        "       The wall clock, egress block, per-process memory and disk quota still hold;\n"
        "       what is lost is the cap on total memory across processes, and CPU."
    )
    return 1


def build() -> int:
    """Build the bundled toolchain images.

    Dependencies live in the image because the sandbox has no egress. Rebuilding is the
    deliberate friction that puts a human between "the agent wants a package" and "the
    package is there".
    """
    for containerfile in sorted(TOOLCHAINS.glob("*/Containerfile")):
        name = containerfile.parent.name
        tag = f"localhost/kuwarden-{name}:1"
        print(f"building {tag} ...")
        result = subprocess.run(
            ["podman", "build", "-t", tag, "-f", str(containerfile), str(containerfile.parent)],
            check=False,
        )
        if result.returncode != 0:
            print(f"  [!!] {tag} failed")
            return result.returncode
        print(f"  [ok] {tag}")
    return 0


async def smoke() -> int:
    """Run one real command end to end, so `doctor` is not the only evidence it works.

    Deliberately relaxes `require_full_isolation`: this is a diagnostic whose whole purpose
    is to prove the plumbing works on *this* machine, and refusing to run would tell the
    operator nothing they did not already learn from `doctor`. It says so out loud, because a
    diagnostic that quietly runs under weaker rules than production is how a false sense of
    readiness starts.
    """
    config = SandboxConfig()
    sandbox = PodmanSandbox(require_full_isolation=False)
    capabilities = await sandbox.capabilities()
    if not capabilities.fully_enforced:
        print("[--] running with cgroup limits unenforced -- diagnostic only, not production")
    async with materialise({"probe.py": "print('sandbox ok')\n"}) as workspace:
        result = await sandbox.exec(
            workspace,
            config.toolchain_image,
            ["python", "probe.py"],
            ResourceLimits(timeout_s=60, tmp_mb=64),
        )
    print(f"exit={result.exit_code} out={result.stdout.strip()!r} limits={result.limits_hit}")
    return 0 if result.succeeded else 1


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "doctor":
        return asyncio.run(doctor())
    if command == "build":
        return build()
    if command == "smoke":
        return asyncio.run(smoke())
    print("usage: python -m engine.sandbox {doctor|build|smoke}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
