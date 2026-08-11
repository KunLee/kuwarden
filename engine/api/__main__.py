"""`uv run python -m engine.api create-user` — bootstrap the first account.

There is deliberately no "first run creates an admin through the web UI" path. That pattern
means any unauthenticated caller who reaches a fresh deployment before its operator does owns
it, and a race for the first request is not an access control.
"""

from __future__ import annotations

import asyncio
import getpass
import sys

from engine.api.auth import Role, create_user, user_count
from engine.devenv import load_dotenv


async def create() -> int:
    """Create an account, prompting for the password so it never reaches shell history."""
    load_dotenv()

    if len(sys.argv) < 4:
        print(
            "usage: python -m engine.api create-user <email> <role>\n"
            f"  role: {' | '.join(r.value for r in Role)}",
            file=sys.stderr,
        )
        return 2

    email, role_name = sys.argv[2], sys.argv[3]
    try:
        role = Role(role_name)
    except ValueError:
        print(f"unknown role {role_name!r}", file=sys.stderr)
        return 2

    existing = await user_count()
    if existing == 0 and role is not Role.ADMIN:
        # A deployment whose only account cannot configure anything is a deployment nobody
        # can finish setting up.
        print("the first account must be an admin", file=sys.stderr)
        return 2

    display_name = input("Display name: ").strip() or email
    # Prompted, never an argument: a password on the command line is in the shell history,
    # the process list, and any shell-integration log.
    password = getpass.getpass("Password (min 12 chars): ")
    if password != getpass.getpass("Repeat: "):
        print("passwords did not match", file=sys.stderr)
        return 1

    try:
        user_id = await create_user(email, display_name, password, role)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"could not create the account: {exc}", file=sys.stderr)
        return 1

    print(f"created {email} ({role.value}) — {user_id}")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "create-user":
        return asyncio.run(create())
    print("usage: python -m engine.api create-user <email> <role>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
