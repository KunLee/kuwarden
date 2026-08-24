"""Roll the target repository back so the same ticket can be run again.

One job: put `main` back where the demo starts, so a rehearsal can be repeated. Everything
else it does — deleting the agent's branches, closing the pull requests it opened, clearing
KuWarden's run rows — exists so the *next* take begins from the same picture as the last one.

**The baseline is a git tag, not a constant in this file.** An earlier version hardcoded a SHA
and it went stale within a day: `main` moved, the constant did not, and a reset would have
rolled the repository backwards past work nobody meant to discard. A tag is re-pointed
deliberately, by a person, with one command.

    uv run python scripts/demo-reset.py --set-baseline   # mark HERE as the start (once)
    uv run python scripts/demo-reset.py                  # report drift, change nothing
    uv run python scripts/demo-reset.py --apply          # roll back and push

**This is a demo tool, not a product feature.** It force-updates a branch and deletes audit
rows — both things nothing in `engine/` is ever allowed to do.

The Azure DevOps ticket is deliberately *not* reset here. Move it back by hand, so a script
cannot fire a webhook delivery you did not intend.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import httpx

REPO = "KunLee/sasagayo"
API = "https://api.github.com"
BRANCH_PREFIX = "kuwarden/"

#: Where the demo starts. Moved only by `--set-baseline`.
BASELINE_TAG = "demo-baseline"


@dataclass
class Drift:
    """What differs from the baseline right now."""

    baseline: str | None
    head: str
    branches: list[str]
    open_prs: list[int]
    runs: int

    @property
    def clean(self) -> bool:
        return (
            self.baseline is not None
            and self.head == self.baseline
            and not self.branches
            and not self.open_prs
        )


def _token() -> str:
    token = os.environ.get("KUWARDEN_SCM_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "no GitHub token in the environment. Export KUWARDEN_SCM_TOKEN (the same "
            "fine-grained token the Workbench holds) for this script only.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return token


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )


def _head(client: httpx.Client) -> str:
    return str(client.get(f"/repos/{REPO}/branches/main").json()["commit"]["sha"])


def _baseline(client: httpx.Client) -> str | None:
    """The commit the tag points at, or None when it has never been set."""
    response = client.get(f"/repos/{REPO}/git/ref/tags/{BASELINE_TAG}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return str(response.json()["object"]["sha"])


def set_baseline(client: httpx.Client) -> str:
    """Point the tag at whatever `main` is now. Deliberate, and separate from `--apply`."""
    sha = _head(client)
    if _baseline(client) is None:
        client.post(
            f"/repos/{REPO}/git/refs",
            json={"ref": f"refs/tags/{BASELINE_TAG}", "sha": sha},
        ).raise_for_status()
    else:
        client.patch(
            f"/repos/{REPO}/git/refs/tags/{BASELINE_TAG}",
            json={"sha": sha, "force": True},
        ).raise_for_status()
    return sha


def inspect(client: httpx.Client, runs: int) -> Drift:
    branches = [
        b["name"]
        for b in client.get(f"/repos/{REPO}/branches", params={"per_page": "100"}).json()
        if b["name"].startswith(BRANCH_PREFIX)
    ]
    prs = [p["number"] for p in client.get(f"/repos/{REPO}/pulls", params={"state": "open"}).json()]
    return Drift(
        baseline=_baseline(client), head=_head(client), branches=branches, open_prs=prs, runs=runs
    )


def roll_back(client: httpx.Client, drift: Drift) -> None:
    """Put `main` back, and clear what the run left behind. Destructive, and noisy about it."""
    assert drift.baseline is not None

    for number in drift.open_prs:
        client.patch(f"/repos/{REPO}/pulls/{number}", json={"state": "closed"})
        print(f"  closed PR #{number}")

    if drift.head != drift.baseline:
        # Force, because the point is to discard the run's merge commit entirely rather than
        # leave a revert in the history that the next recording would show.
        client.patch(
            f"/repos/{REPO}/git/refs/heads/main",
            json={"sha": drift.baseline, "force": True},
        ).raise_for_status()
        print(f"  main rolled back {drift.head[:12]} -> {drift.baseline[:12]}")
    else:
        print("  main already at the baseline")

    for branch in drift.branches:
        client.delete(f"/repos/{REPO}/git/refs/heads/{branch}")
        print(f"  deleted {branch}")


async def _run_count() -> int:
    from engine.db import connect

    async with connect() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM flow_runs") or 0)


async def purge_runs() -> int:
    """Clear KuWarden's run rows so the Workbench Runs list is empty on the next take.

    `flow_events` carries the append-only trigger from invariant 9, so it is disabled for
    exactly these statements and re-enabled in a `finally`. Nothing in `engine/` may do this;
    a demo reset script may, and only because the rows it removes are its own rehearsals.
    """
    from engine.db import connect

    async with connect() as conn, conn.transaction():
        await conn.execute("ALTER TABLE flow_events DISABLE TRIGGER flow_events_no_update")
        try:
            removed = await conn.fetchval("SELECT count(*) FROM flow_runs")
            await conn.execute("DELETE FROM flow_events")
            await conn.execute("DELETE FROM flow_runs")
        finally:
            await conn.execute("ALTER TABLE flow_events ENABLE TRIGGER flow_events_no_update")
    return int(removed or 0)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Roll the demo repository back for a retake.")
    parser.add_argument(
        "--apply", action="store_true", help="roll back and push (default: report only)"
    )
    parser.add_argument(
        "--set-baseline", action="store_true", help=f"point {BASELINE_TAG} at main as it is now"
    )
    parser.add_argument("--keep-runs", action="store_true", help="leave flow_runs alone")
    args = parser.parse_args()

    from engine.devenv import load_dotenv

    load_dotenv()

    with _client(_token()) as client:
        if args.set_baseline:
            sha = set_baseline(client)
            print(f"{BASELINE_TAG} now points at {sha[:12]} — a reset returns main to here")
            return 0

        drift = inspect(client, await _run_count())

        if drift.baseline is None:
            print(
                f"no {BASELINE_TAG} tag on {REPO}. Run --set-baseline once, with main in the "
                "state every take should start from."
            )
            return 2

        state = "(baseline)" if drift.head == drift.baseline else f"ahead of {drift.baseline[:12]}"
        branches = ", ".join(drift.branches) or "none"
        open_prs = ", ".join(f"#{n}" for n in drift.open_prs) or "none"
        print(f"main        {drift.head[:12]}  {state}")
        print(f"branches    {branches}")
        print(f"open PRs    {open_prs}")
        print(f"flow_runs   {drift.runs}")

        if not args.apply:
            print("\nreport only. Re-run with --apply to roll back.")
            return 0 if drift.clean else 1

        print("\nrolling back:")
        roll_back(client, drift)

    if not args.keep_runs:
        print(f"  cleared {await purge_runs()} run(s) from KuWarden")

    print("\nVercel will redeploy the baseline on its own.")
    print("Still to do by hand: move the Azure DevOps ticket back to its starting state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
