"""Tenant credentials, encrypted with a local master key — ADR 0006.

What this protects: a stolen database backup, an exfiltrated dump, a misconfigured read
replica, a `SELECT` through injection. What it does not protect: a compromised host, because
the key is on the host. That limit is real and deliberate; see ADR 0006 §2 rather than
assuming otherwise.

The master key never appears in a log, a traceback, or an audit row. Neither does a decrypted
value — `Secret` refuses to render itself, and nothing here returns a bare string.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets as _secrets
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from engine.adapters.credentials import (
    CredentialKind,
    CredentialRequest,
    Secret,
)
from engine.db import connect
from engine.errors import KuWardenError, PolicyDenied

KEY_VARIABLE = "KUWARDEN_SECRET_KEY"
KEY_BYTES = 32
NONCE_BYTES = 12


class SecretKeyError(KuWardenError):
    """The master key is absent, malformed, or the wrong one for this ciphertext."""


class CredentialStore(Protocol):
    """Read *and* write. Deliberately separate from `CredentialBroker`.

    Only the Workbench holds this. A node that could call `put` is a node that could grant
    itself access, which is the failure ADR 0001 exists to prevent.
    """

    async def resolve(self, request: CredentialRequest) -> Secret: ...

    async def put(self, app_id: UUID, kind: CredentialKind, secret: Secret) -> None: ...

    async def forget(self, app_id: UUID, kind: CredentialKind) -> None: ...

    async def kinds_present(self, app_id: UUID) -> list[CredentialKind]: ...


def generate_master_key() -> str:
    """A new master key, for `KUWARDEN_SECRET_KEY`. Printed once, never stored by us."""
    return base64.urlsafe_b64encode(_secrets.token_bytes(KEY_BYTES)).decode()


def _load_master_key(raw: str | None = None) -> bytes:
    material = raw if raw is not None else os.environ.get(KEY_VARIABLE)
    if not material:
        raise SecretKeyError(
            f"{KEY_VARIABLE} is not set. Generate one with "
            "`uv run python -m engine.adapters.secrets keygen` and put it in .env — "
            "losing it means every stored credential must be re-entered."
        )
    try:
        key = base64.urlsafe_b64decode(material)
    except (ValueError, TypeError):
        raise SecretKeyError(f"{KEY_VARIABLE} is not valid base64url") from None
    if len(key) != KEY_BYTES:
        raise SecretKeyError(f"{KEY_VARIABLE} must decode to {KEY_BYTES} bytes, got {len(key)}")
    return key


def load_master_key(raw: str | None = None) -> bytes:
    """The master key, as bytes. Public entry point for anything that needs to derive from it."""
    return _load_master_key(raw)


def key_fingerprint(key: bytes) -> str:
    """Identifies a key without revealing it, so rotation can tell rows apart."""
    return hashlib.sha256(b"kuwarden-key-id\x00" + key).hexdigest()[:16]


def _associated_data(app_id: UUID, kind: CredentialKind) -> bytes:
    """Binds a ciphertext to its slot.

    Without this, someone with database write access could copy one application's ciphertext
    into another application's row and it would decrypt cleanly — silently handing the second
    application the first one's access. With it, that row fails to decrypt.
    """
    return f"kuwarden/v1/{app_id}/{kind.value}".encode()


class EncryptedPostgresStore:
    """Implements `CredentialStore` over `app_credentials`.

    There is no read-back API above `resolve`: the Workbench can store a credential and can
    ask whether one exists, and cannot retrieve it. A credential that can be read back
    through a UI is a credential that eventually is.
    """

    def __init__(self, app_id: UUID, master_key: str | None = None) -> None:
        # Scoped to one application at construction, so a query cannot accidentally reach
        # another tenant's row.
        self._app_id = app_id
        self._key = _load_master_key(master_key)
        self._key_id = key_fingerprint(self._key)

    def _aead(self) -> AESGCM:
        return AESGCM(self._key)

    async def resolve(self, request: CredentialRequest) -> Secret:
        async with connect() as conn:
            row = await conn.fetchrow(
                "SELECT key_id, nonce, ciphertext FROM app_credentials "
                "WHERE app_id = $1 AND kind = $2",
                self._app_id,
                request.kind.value,
            )
        if row is None:
            raise PolicyDenied(
                f"no credential stored for {request.kind.value} on application {self._app_id}"
            )
        if row["key_id"] != self._key_id:
            # A different key wrote this. Say so plainly rather than surfacing a decryption
            # failure that reads like corruption.
            raise SecretKeyError(
                f"{request.kind.value} was encrypted with key {row['key_id']}, but "
                f"{KEY_VARIABLE} is key {self._key_id}; the credential must be re-entered "
                "or the original key restored"
            )

        try:
            plaintext = self._aead().decrypt(
                bytes(row["nonce"]),
                bytes(row["ciphertext"]),
                _associated_data(self._app_id, request.kind),
            )
        except Exception:
            raise SecretKeyError(
                f"{request.kind.value} failed to decrypt; the ciphertext has been altered "
                "or moved between rows"
            ) from None
        return Secret(plaintext.decode())

    async def put(self, app_id: UUID, kind: CredentialKind, secret: Secret) -> None:
        if app_id != self._app_id:
            raise PolicyDenied(
                f"store is scoped to application {self._app_id}, refusing a write to {app_id}"
            )
        nonce = _secrets.token_bytes(NONCE_BYTES)
        ciphertext = self._aead().encrypt(
            nonce, secret.reveal().encode(), _associated_data(app_id, kind)
        )
        async with connect() as conn:
            await conn.execute(
                """
                INSERT INTO app_credentials (app_id, kind, key_id, nonce, ciphertext)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (app_id, kind) DO UPDATE
                    SET key_id = EXCLUDED.key_id,
                        nonce = EXCLUDED.nonce,
                        ciphertext = EXCLUDED.ciphertext,
                        updated_at = now()
                """,
                app_id,
                kind.value,
                self._key_id,
                nonce,
                ciphertext,
            )

    async def forget(self, app_id: UUID, kind: CredentialKind) -> None:
        async with connect() as conn:
            await conn.execute(
                "DELETE FROM app_credentials WHERE app_id = $1 AND kind = $2",
                app_id,
                kind.value,
            )

    async def kinds_present(self, app_id: UUID) -> list[CredentialKind]:
        """Which credentials exist — never their values. This is what a UI may show."""
        async with connect() as conn:
            rows = await conn.fetch(
                "SELECT kind FROM app_credentials WHERE app_id = $1 ORDER BY kind", app_id
            )
        return [CredentialKind(row["kind"]) for row in rows]


def _keygen_cli() -> int:
    """`uv run python -m engine.adapters.secrets keygen`."""
    import sys

    if len(sys.argv) < 2 or sys.argv[1] != "keygen":
        print("usage: python -m engine.adapters.secrets keygen", file=sys.stderr)
        return 2
    # Printed once, to stdout, never written anywhere by us.
    print(f"{KEY_VARIABLE}={generate_master_key()}")
    print(
        "\nAppend that to .env. Back it up somewhere other than your database backup — "
        "a key stored beside the ciphertext it protects is not encryption.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_keygen_cli())
