"""Local accounts, password hashing, and session identity.

Built in rather than delegated to an external identity provider, because the flagship
deployment is air-gapped and can reach none. An enterprise IdP adapter is a later addition
alongside this, not a replacement for it.

The session key is **derived** from `KUWARDEN_SECRET_KEY` rather than being a second secret.
One key to back up is one key to lose; HKDF with a distinct info string keeps the session
signing key cryptographically independent of the credential-encryption key, so compromising
one does not yield the other.
"""

from __future__ import annotations

import base64
import uuid
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Depends, HTTPException, Request

from engine.adapters.secrets import KEY_VARIABLE, load_master_key
from engine.db import connect

#: Defaults follow argon2-cffi's current recommendation. The encoded hash carries its own
#: parameters, so raising these later re-hashes on next login rather than locking anyone out.
HASHER = PasswordHasher()

SESSION_COOKIE = "kuwarden_session"
#: Long enough not to interrupt a working day, short enough that a stolen laptop is not a
#: standing grant. Revocation does not wait for it — see `token_version`.
SESSION_MAX_AGE_S = 12 * 60 * 60


class Role(StrEnum):
    """ADR 0003 §1. Ordered: each role includes the ones before it."""

    VIEWER = "viewer"
    APPROVER = "approver"
    ADMIN = "admin"


_RANK = {Role.VIEWER: 0, Role.APPROVER: 1, Role.ADMIN: 2}


@dataclass(frozen=True)
class Principal:
    """Who is making a request. The start of ADR 0003's delegation chain."""

    id: uuid.UUID
    email: str
    display_name: str
    role: Role

    def can(self, required: Role) -> bool:
        return _RANK[self.role] >= _RANK[required]


def session_signing_key() -> str:
    """Derive the session key from the master key.

    HKDF with a distinct `info` means the session signing key and the credential-encryption
    key are independent: recovering one tells an attacker nothing about the other, even
    though the operator only has one secret to store and back up.
    """
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"kuwarden/session-signing/v1",
    ).derive(load_master_key())
    return base64.urlsafe_b64encode(derived).decode()


def hash_password(password: str) -> str:
    """Argon2id. Returns the encoded hash including its parameters."""
    if len(password) < 12:
        # Length is the only requirement worth enforcing. Composition rules push people
        # towards predictable substitutions and are no longer recommended practice.
        raise ValueError("password must be at least 12 characters")
    return HASHER.hash(password)


#: Verified against when no user matches, so a request for an unknown address takes about as
#: long as one for a known address. Without this, response time enumerates valid accounts.
_DUMMY_HASH = HASHER.hash("a-password-that-matches-nothing")


async def authenticate(email: str, password: str) -> Principal | None:
    """Check an email and password. Returns None for every kind of failure.

    Deliberately does not distinguish "no such user" from "wrong password" from "disabled" in
    its return value: telling an unauthenticated caller which one it was is an account
    enumeration oracle.
    """
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, display_name, password_hash, role, disabled_at "
            "FROM users WHERE email = $1",
            email.strip().lower(),
        )

    if row is None:
        # Burn comparable time rather than returning early.
        with suppress(VerifyMismatchError, InvalidHashError):
            HASHER.verify(_DUMMY_HASH, password)
        return None

    try:
        HASHER.verify(row["password_hash"], password)
    except (VerifyMismatchError, InvalidHashError):
        return None

    if row["disabled_at"] is not None:
        return None

    if HASHER.check_needs_rehash(row["password_hash"]):
        # Parameters were raised since this password was set. Upgrade transparently.
        async with connect() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = $2 WHERE id = $1",
                row["id"],
                HASHER.hash(password),
            )

    async with connect() as conn:
        await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", row["id"])

    return Principal(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=Role(row["role"]),
    )


async def current_principal(request: Request) -> Principal:
    """Resolve the caller, or 401.

    The session carries `token_version`, and it is re-checked against the database on every
    request. That is a query per request, and it is the price of revocation taking effect
    immediately rather than whenever a cookie expires — the same deny-wins instinct as
    ADR 0003 §5.
    """
    session: dict[str, Any] = getattr(request, "session", {})
    user_id = session.get("user_id")
    if not user_id:
        raise HTTPException(401, "not signed in")

    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, display_name, role, token_version, disabled_at "
            "FROM users WHERE id = $1",
            uuid.UUID(user_id),
        )

    if row is None or row["disabled_at"] is not None:
        session.clear()
        raise HTTPException(401, "account is not active")
    if row["token_version"] != session.get("token_version"):
        session.clear()
        raise HTTPException(401, "session has been revoked")

    return Principal(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=Role(row["role"]),
    )


def requires(role: Role):  # type: ignore[no-untyped-def]
    """Dependency factory gating an endpoint on a minimum role.

    Applied per endpoint rather than globally, so adding a route without deciding who may
    call it is a visible omission in the diff rather than an accidental grant.
    """

    async def guard(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if not principal.can(role):
            raise HTTPException(
                403,
                f"this action requires the {role.value} role; {principal.email} is "
                f"{principal.role.value}",
            )
        return principal

    return guard


async def create_user(email: str, display_name: str, password: str, role: Role) -> uuid.UUID:
    """Create an account. Used by the bootstrap CLI and by admins in the Workbench."""
    user_id = uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, display_name, password_hash, role) "
            "VALUES ($1,$2,$3,$4,$5)",
            user_id,
            email.strip().lower(),
            display_name,
            hash_password(password),
            role.value,
        )
    return user_id


async def user_count() -> int:
    async with connect() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM users") or 0)


__all__ = [
    "KEY_VARIABLE",
    "SESSION_COOKIE",
    "SESSION_MAX_AGE_S",
    "Principal",
    "Role",
    "authenticate",
    "create_user",
    "current_principal",
    "hash_password",
    "requires",
    "session_signing_key",
    "user_count",
]
