"""PostgreSQL access. Plain SQL, applied in order, recorded in a table.

No migration framework: this ships into air-gapped environments and every dependency is
someone's security review. Revisit when a migration needs to be reversible or generated.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

MIGRATIONS = Path(__file__).parent / "migrations"


def dsn() -> str:
    """Twelve-factor: one variable, no host paths, same string in every environment."""
    return os.environ.get(
        "KUWARDEN_DATABASE_URL",
        "postgresql://kuwarden:dev@127.0.0.1:5432/kuwarden",
    )


@asynccontextmanager
async def connect() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(dsn())
    try:
        yield conn
    finally:
        await conn.close()


async def migrate(conn: asyncpg.Connection) -> list[str]:
    """Apply every unapplied migration in filename order. Returns what was applied."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}

    applied: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in done:
            continue
        # One transaction per migration: a half-applied schema is worse than a failed run.
        async with conn.transaction():
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
        applied.append(path.name)
    return applied
