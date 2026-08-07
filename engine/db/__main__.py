"""`uv run python -m engine.db migrate`."""

from __future__ import annotations

import asyncio
import sys

from engine.db import connect, dsn, migrate


async def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "migrate":
        print("usage: python -m engine.db migrate", file=sys.stderr)
        return 2
    async with connect() as conn:
        applied = await migrate(conn)
    if applied:
        print(f"applied to {dsn().rsplit('@', 1)[-1]}: " + ", ".join(applied))
    else:
        print("already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
