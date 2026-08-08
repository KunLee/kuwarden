"""PostgreSQL access. Plain SQL, applied in order, recorded in a table.

No migration framework: this ships into air-gapped environments and every dependency is
someone's security review. Revisit when a migration needs to be reversible or generated.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import asyncpg

from engine.errors import PolicyDenied

MIGRATIONS = Path(__file__).parent / "migrations"


def dsn() -> str:
    """Twelve-factor: one variable, no host paths, same string in every environment.

    No embedded default password. `EnvCredentialBroker` denies a missing credential rather
    than defaulting one, and a database password is not a lesser credential than a PAT — a
    development default that works out of the box is a development default that reaches
    somewhere it shouldn't, because nobody changes what already works.
    """
    url = os.environ.get("KUWARDEN_DATABASE_URL")
    if url:
        return url

    password = os.environ.get("KUWARDEN_POSTGRES_PASSWORD")
    if not password:
        raise PolicyDenied(
            "no database password available: set KUWARDEN_POSTGRES_PASSWORD (see .env.example) "
            "or KUWARDEN_DATABASE_URL"
        )
    user = os.environ.get("KUWARDEN_POSTGRES_USER", "kuwarden")
    database = os.environ.get("KUWARDEN_POSTGRES_DB", "kuwarden")
    host = os.environ.get("KUWARDEN_POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("KUWARDEN_POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{quote(password, safe='')}@{host}:{port}/{database}"


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
