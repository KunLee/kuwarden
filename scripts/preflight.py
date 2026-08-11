"""Everything that must be true before a run can start, checked in one place.

Written in Python rather than in the shell script that calls it for two reasons: the checks
need `engine.config` and `engine.db` anyway, and a summary assembled by string-quoting through
PowerShell is a summary that breaks the first time a project name contains a space.

Prints what the worker will actually load. That is not decoration — the trigger is currently
declared in *two* places, here and in the Workbench, and they must agree by hand. An operator
who cannot see what the worker read has no way to notice they disagree.

Exit codes: 0 ready, 1 a problem with a stated fix, 2 could not check.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from engine.config import AppConfig, ConfigError, load
from engine.db import connect
from engine.devenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "kuwarden.yaml"


def _bad(problem: str, fix: str) -> int:
    print(f"  [!!] {problem}")
    print(f"\n  Fix:\n    {fix}\n")
    return 1


def _config() -> AppConfig | int:
    if not CONFIG.is_file():
        return _bad(
            f"no {CONFIG.name}",
            "cp kuwarden.example.yaml kuwarden.yaml   # then fill in org/repo and the trigger",
        )
    try:
        return load(CONFIG)
    except ConfigError as exc:
        return _bad(f"{CONFIG.name} does not load: {exc}", "Correct the file and re-run.")


def _describe(config: AppConfig) -> None:
    """What the worker will use. Printed so a disagreement with the Workbench is visible."""
    trigger = config.triggers[0] if config.triggers else None
    print(f"  application    {config.name}")
    print(
        f"  repository     {config.primary.provider} "
        f"{config.primary.org}/{config.primary.repo}"
    )
    if trigger is None:
        print("  tickets        NONE DECLARED — triage refuses every run")
    else:
        where = trigger.organisation or trigger.site or "?"
        print(f"  tickets        {trigger.provider} {where}/{trigger.project}")
        # Every admission rule, including the ones left off. "any state" is a posture,
        # not a blank — and it is the one that makes a webhook expensive.
        rules = [
            f"label={trigger.label!r}" if trigger.label else "ANY label",
            f"state={trigger.ready_state!r}" if trigger.ready_state else "ANY state",
            f"max_points={trigger.max_story_points}",
        ]
        print(f"  admits         {', '.join(rules)}")
    print(f"  control point  {config.integration_model.value}")
    print(f"  toolchain      {config.sandbox.toolchain_image}")
    print(f"  test command   {' '.join(config.sandbox.test_command)}")
    if config.ci is None:
        print("  ci anchor      none — the sandbox verdict stands, and says so on the gate")
    else:
        print(f"  ci anchor      {config.ci.provider}")
    print(f"  models         {config.llm.provider.value if config.llm else 'NONE — coder fails'}")


async def _accounts() -> list[tuple[str, str]]:
    async with connect() as conn:
        rows = await conn.fetch("SELECT email, role FROM users WHERE disabled_at IS NULL")
    return [(row["email"], row["role"]) for row in rows]


async def main() -> int:
    load_dotenv()

    print("\n=== Configuration the worker will load")
    config = _config()
    if isinstance(config, int):
        return config
    _describe(config)

    if config.llm is None:
        return _bad(
            "no llm section — the Planner and Coder both need a model",
            "Add an `llm:` block to kuwarden.yaml (see kuwarden.example.yaml).",
        )

    print("\n=== Accounts")
    try:
        accounts = await _accounts()
    except Exception as exc:  # noqa: BLE001 — any failure here means "cannot check"
        print(f"  [!!] could not read the users table: {exc}")
        return 2

    if not accounts:
        print("  [--] none — the Workbench cannot be signed into")
        print("\n  Create the first one (the password is prompted, never an argument):")
        print("    uv run python -m engine.api create-user you@example.com admin\n")
        return 0

    for email, role in sorted(accounts):
        print(f"  [ok] {email}  ({role})")
    print(
        "\n  If none of these are yours, add your own — the 'first account must be an admin'\n"
        "  rule only applies to an empty deployment:\n"
        "    uv run python -m engine.api create-user you@example.com admin"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
