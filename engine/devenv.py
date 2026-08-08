"""Loading `.env` for local development.

Compose reads `.env` on its own; a process started with `uv run` does not. Rather than a
dependency or an import-time side effect, this is called explicitly from the CLI entry points
and from the test suite — three places, all of them development.

Nothing in `engine/` calls it implicitly. A deployed worker gets its environment from the
platform, and a module that quietly reads a file from the working directory is a module that
behaves differently depending on where it was started.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> list[str]:
    """Set any variable in `.env` that is not already in the environment.

    A real environment variable always wins, so exporting one overrides the file rather than
    silently losing to it. Returns the names that were set, never the values.
    """
    file = Path(path)
    if not file.exists():
        return []

    applied: list[str] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip("'\"")
            applied.append(name)
    return applied
