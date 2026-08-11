"""Wipe the development database's run and application data.

**Development only.** This deletes rows from `flow_events`, which invariant 9 says is
append-only and which a database trigger enforces. The script disables that trigger to do it.
That is defensible on a laptop whose data came from a test suite and nowhere else; it would be
indefensible anywhere a real run has ever happened, which is why this lives in `scripts/` and
not behind an API endpoint.

Order matters: `flow_runs.app_id` is a foreign key into `app_registry`, and `flow_events`
references `flow_runs`. Deleting the parent first fails rather than cascading.

Accounts are left alone apart from the `@acme.test` fixtures, because an operator who loses
their own login to a data reset has to go back to the bootstrap CLI.
"""

from __future__ import annotations

import argparse
import asyncio

from engine.db import connect
from engine.devenv import load_dotenv

#: Left in place unless --all is passed. Everything else in `app_registry` came from a test.
TEST_REPO_URLS = ("https://example.invalid/test-app", "https://example.invalid/x")

#: Fixture accounts from `test_workbench_api.py`, which an operator will never recognise and
#: whose passwords nobody has.
FIXTURE_EMAILS = ("admin@acme.test", "dev@acme.test")


async def counts(conn: object) -> dict[str, int]:
    tables = ("app_registry", "app_triggers", "app_credentials", "flow_runs", "flow_events")
    return {t: int(await conn.fetchval(f"SELECT count(*) FROM {t}") or 0) for t in tables}  # type: ignore[attr-defined]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="delete every application, not only the ones a test created",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    load_dotenv()

    async with connect() as conn:
        before = await counts(conn)
        print("before:", ", ".join(f"{k}={v}" for k, v in before.items()))

        if args.all:
            doomed = await conn.fetch("SELECT id, name FROM app_registry")
        else:
            doomed = await conn.fetch(
                "SELECT id, name FROM app_registry WHERE repo_url = ANY($1::text[])",
                list(TEST_REPO_URLS),
            )
        print(f"\nwill delete {len(doomed)} application(s) and every run belonging to them")
        if not args.all:
            keeping = await conn.fetch(
                "SELECT name FROM app_registry WHERE repo_url <> ALL($1::text[])",
                list(TEST_REPO_URLS),
            )
            if keeping:
                print("keeping: " + ", ".join(sorted(r["name"] for r in keeping)))

        if not args.yes and input("\ntype 'delete' to proceed: ").strip() != "delete":
            print("nothing was deleted")
            return 1

        ids = [row["id"] for row in doomed]
        async with conn.transaction():
            # The append-only trigger is the control, not an obstacle — it is turned off for
            # exactly this statement and turned back on inside the same transaction, so a
            # failure mid-way cannot leave the table unprotected.
            await conn.execute("ALTER TABLE flow_events DISABLE TRIGGER flow_events_no_update")
            try:
                await conn.execute(
                    "DELETE FROM flow_events WHERE run_id IN "
                    "(SELECT id FROM flow_runs WHERE app_id = ANY($1::uuid[]))",
                    ids,
                )
                await conn.execute("DELETE FROM flow_runs WHERE app_id = ANY($1::uuid[])", ids)
            finally:
                await conn.execute("ALTER TABLE flow_events ENABLE TRIGGER flow_events_no_update")

            await conn.execute("DELETE FROM app_credentials WHERE app_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM app_triggers WHERE app_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM app_registry WHERE id = ANY($1::uuid[])", ids)

            removed = await conn.execute(
                "DELETE FROM users WHERE email = ANY($1::text[])", list(FIXTURE_EMAILS)
            )

        after = await counts(conn)
        print("\nafter: ", ", ".join(f"{k}={v}" for k, v in after.items()))
        print(f"fixture accounts removed: {removed}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
