"""`uv run python -m engine.sandbox doctor|build|build-app|smoke`.

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

#: Generic images KuWarden ships. One set of tools grades any project in that language,
#: so these are built once, by `build`, and shared across applications.
TOOLCHAINS = Path(__file__).parent / "toolchains"

#: Recipes for images that must carry ONE application's dependencies, built by
#: `build-app`. Kept out of `toolchains/` so that `build` cannot pick them up: they have
#: no meaning without a manifest, and a recipe that fails for everyone who runs the
#: ordinary build command is a recipe in the wrong directory.
RECIPES = Path(__file__).parent / "recipes"


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


def build_app(argv: list[str]) -> int:
    """Build a toolchain image carrying one application's dependencies.

    `--recipe` names a directory under `recipes/`, `--name` becomes the image tag, and
    `--manifest` is a directory holding the application's dependency manifest — for `node20`,
    its `package.json` and, if it has one, `package-lock.json`.

    The manifest is copied into a temporary build context rather than the application checkout
    being used as one. Two reasons, and the second is the one that bites: a repository is
    usually far larger than the two files needed, and `podman build` would upload all of it;
    and a `.dockerignore` or stray `node_modules` in that checkout would silently change what
    the image contains.

    Returns a non-zero exit code rather than raising, matching the other commands here — this
    is a CLI, and a traceback is a worse answer than a sentence.
    """
    import shutil
    import tempfile

    options = dict(zip(argv[::2], argv[1::2], strict=False))
    recipe, name = options.get("--recipe", ""), options.get("--name", "")
    manifest = options.get("--manifest", "")
    if not recipe or not name or not manifest:
        print(
            "usage: python -m engine.sandbox build-app --recipe node20 --name NAME "
            "--manifest DIR\n"
            f"       recipes available: {', '.join(sorted(p.name for p in RECIPES.iterdir()))}",
            file=sys.stderr,
        )
        return 2

    containerfile = RECIPES / recipe / "Containerfile"
    if not containerfile.is_file():
        print(f"no recipe {recipe!r} in {RECIPES}", file=sys.stderr)
        return 2

    source = Path(manifest)
    wanted = ["package.json", "package-lock.json"] if recipe == "node20" else []
    if not (source / wanted[0]).is_file():
        print(f"{source / wanted[0]} does not exist", file=sys.stderr)
        return 2

    tag = f"localhost/kuwarden-app-{name}:1"
    with tempfile.TemporaryDirectory() as context:
        root = Path(context)
        shutil.copy(containerfile, root / "Containerfile")
        for filename in wanted:
            if (source / filename).is_file():
                shutil.copy(source / filename, root / filename)
            else:
                # An empty stub, so the recipe's `COPY package-lock.json*` and its `-s` test
                # both behave without the Containerfile having to branch on existence.
                (root / filename).write_text("", encoding="utf-8")

        print(f"building {tag} from {recipe} with the manifest in {source} ...")
        result = subprocess.run(
            ["podman", "build", "-t", tag, "-f", str(root / "Containerfile"), str(root)],
            check=False,
        )

    if result.returncode != 0:
        print(f"  [!!] {tag} failed", file=sys.stderr)
        return result.returncode

    print(f"  [ok] {tag}\n")
    print("Point the application's kuwarden.yaml at it:\n")
    print(f"  toolchain_image: {tag}")
    print('  test_command: [sh, -c, "npm run lint && npm run typecheck"]\n')
    print(
        "Dependencies live at /node_modules, which Node finds by walking up from the\n"
        "project. Nothing is written into the workspace on purpose: a symlink placed there\n"
        "lands in the host directory git reads to compute the diff, and breaks it with\n"
        '"unable to index file \'node_modules\'" after the Coder has already finished.'
    )
    return 0


async def smoke() -> int:
    """Run one real command end to end, so `doctor` is not the only evidence it works.

    Deliberately relaxes `require_full_isolation`: this is a diagnostic whose whole purpose
    is to prove the plumbing works on *this* machine, and refusing to run would tell the
    operator nothing they did not already learn from `doctor`. It says so out loud, because a
    diagnostic that quietly runs under weaker rules than production is how a false sense of
    readiness starts.
    """
    # The diagnostic's own image and command, not an application's. `SandboxConfig` no
    # longer defaults these — an application must declare them — but `smoke` is proving
    # that this host can run a container at all, so it names the shipped Python image
    # explicitly rather than pretending to be some application.
    config = SandboxConfig(
        toolchain_image="localhost/kuwarden-python312:1",
        test_command=["python", "-c", "print('sandbox ok')"],
    )
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
    if command == "build-app":
        return build_app(sys.argv[2:])
    if command == "smoke":
        return asyncio.run(smoke())
    print(
        "usage: python -m engine.sandbox {doctor|build|smoke}\n"
        "       python -m engine.sandbox build-app --recipe node20 --name NAME "
        "--manifest DIR",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
