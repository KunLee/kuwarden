"""Encrypted credential storage — ADR 0006.

These need the stack up, because encryption that works against a mock and not against real
`BYTEA` round-tripping is encryption that fails on the first save.
"""

from __future__ import annotations

import uuid

import pytest

from engine.adapters.credentials import CredentialKind, CredentialRequest, Secret
from engine.adapters.secrets import (
    EncryptedPostgresStore,
    SecretKeyError,
    generate_master_key,
    key_fingerprint,
)
from engine.db import connect, migrate
from engine.errors import PolicyDenied
from tests.conftest import track_application

KEY = generate_master_key()
OTHER_KEY = generate_master_key()


async def _register() -> uuid.UUID:
    app_id = uuid.uuid4()
    try:
        async with connect() as conn:
            await migrate(conn)
            await conn.execute(
                "INSERT INTO app_registry (id, name, repo_url, integration_model) "
                "VALUES ($1,$2,$3,'gated_deployment')",
                app_id,
                f"cred-test-{app_id.hex[:8]}",
                "https://example.invalid/x",
            )
    except Exception as exc:  # noqa: BLE001 - any failure here means "infra absent"
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    # Tracked so the session teardown removes it, along with the credentials stored against it.
    return track_application(app_id)


async def test_a_credential_round_trips() -> None:
    app_id = await _register()
    store = EncryptedPostgresStore(app_id, master_key=KEY)

    await store.put(app_id, CredentialKind.SCM_WRITE_BRANCH, Secret("ghp_realtoken"))
    resolved = await store.resolve(
        CredentialRequest(kind=CredentialKind.SCM_WRITE_BRANCH, realm="github.com:acme")
    )
    assert resolved.reveal() == "ghp_realtoken"


async def test_the_plaintext_is_not_in_the_database() -> None:
    """The point of the exercise. A stolen dump must be useless."""
    app_id = await _register()
    store = EncryptedPostgresStore(app_id, master_key=KEY)
    await store.put(app_id, CredentialKind.TICKET_READ_WRITE, Secret("jira_secret_value"))

    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT ciphertext, key_id FROM app_credentials WHERE app_id = $1", app_id
        )
    assert row is not None
    assert b"jira_secret_value" not in bytes(row["ciphertext"])
    assert row["key_id"] == key_fingerprint(__import__("base64").urlsafe_b64decode(KEY))


async def test_a_ciphertext_moved_between_rows_fails_to_decrypt() -> None:
    """Associated data. Without it this would decrypt cleanly and grant the wrong access."""
    victim = await _register()
    attacker = await _register()

    await EncryptedPostgresStore(victim, master_key=KEY).put(
        victim, CredentialKind.SCM_WRITE_BRANCH, Secret("ghp_victim")
    )

    # Someone with database write access copies the row across.
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT key_id, nonce, ciphertext FROM app_credentials WHERE app_id = $1", victim
        )
        assert row is not None
        await conn.execute(
            "INSERT INTO app_credentials (app_id, kind, key_id, nonce, ciphertext) "
            "VALUES ($1,'scm.write_branch',$2,$3,$4)",
            attacker,
            row["key_id"],
            row["nonce"],
            row["ciphertext"],
        )

    with pytest.raises(SecretKeyError, match="altered or moved"):
        await EncryptedPostgresStore(attacker, master_key=KEY).resolve(
            CredentialRequest(kind=CredentialKind.SCM_WRITE_BRANCH, realm="github.com:acme")
        )


async def test_the_wrong_key_says_so_rather_than_reading_as_corruption() -> None:
    app_id = await _register()
    await EncryptedPostgresStore(app_id, master_key=KEY).put(
        app_id, CredentialKind.LLM_API_KEY, Secret("sk-ant-x")
    )

    with pytest.raises(SecretKeyError, match="must be re-entered or the original key restored"):
        await EncryptedPostgresStore(app_id, master_key=OTHER_KEY).resolve(
            CredentialRequest(kind=CredentialKind.LLM_API_KEY, realm="anthropic")
        )


async def test_a_missing_credential_is_denied_not_defaulted() -> None:
    app_id = await _register()
    with pytest.raises(PolicyDenied, match="no credential stored"):
        await EncryptedPostgresStore(app_id, master_key=KEY).resolve(
            CredentialRequest(kind=CredentialKind.DEPLOY, realm="k8s")
        )


async def test_a_store_refuses_to_write_outside_its_application() -> None:
    mine = await _register()
    theirs = await _register()
    with pytest.raises(PolicyDenied, match="refusing a write"):
        await EncryptedPostgresStore(mine, master_key=KEY).put(
            theirs, CredentialKind.SCM_READ, Secret("x")
        )


async def test_presence_is_listable_but_values_are_not() -> None:
    """What a UI is allowed to show."""
    app_id = await _register()
    store = EncryptedPostgresStore(app_id, master_key=KEY)
    await store.put(app_id, CredentialKind.SCM_READ, Secret("a"))
    await store.put(app_id, CredentialKind.TICKET_READ_WRITE, Secret("b"))

    present = await store.kinds_present(app_id)
    assert set(present) == {CredentialKind.SCM_READ, CredentialKind.TICKET_READ_WRITE}
    assert not hasattr(store, "reveal_all")


async def test_a_rewrite_replaces_rather_than_duplicating() -> None:
    app_id = await _register()
    store = EncryptedPostgresStore(app_id, master_key=KEY)
    await store.put(app_id, CredentialKind.SCM_READ, Secret("first"))
    await store.put(app_id, CredentialKind.SCM_READ, Secret("second"))

    resolved = await store.resolve(
        CredentialRequest(kind=CredentialKind.SCM_READ, realm="github.com:acme")
    )
    assert resolved.reveal() == "second"


def test_a_missing_master_key_is_an_error_not_a_default() -> None:
    with pytest.raises(SecretKeyError, match="is not set"):
        EncryptedPostgresStore(uuid.uuid4(), master_key="")


def test_a_malformed_master_key_is_rejected() -> None:
    with pytest.raises(SecretKeyError, match="must decode to 32 bytes"):
        EncryptedPostgresStore(uuid.uuid4(), master_key="c2hvcnQ=")


# --- resolution order at run time -------------------------------------------------------------


async def test_the_store_wins_over_a_stale_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Workbench writes at run time; the environment is fixed at process start.

    A credential someone just entered must beat a variable exported months ago, or rotating a
    token through the UI appears to work and changes nothing.
    """
    from engine.activities.nodes import StoreThenEnvBroker

    app_id = await _register()
    store = EncryptedPostgresStore(app_id, master_key=KEY)
    await store.put(app_id, CredentialKind.SCM_READ, Secret("from-the-store"))

    monkeypatch.setenv("KUWARDEN_SCM_TOKEN", "from-the-environment")
    monkeypatch.setenv("KUWARDEN_SECRET_KEY", KEY)

    resolved = await StoreThenEnvBroker(app_id).resolve(
        CredentialRequest(kind=CredentialKind.SCM_READ, realm="github.com/acme")
    )
    assert resolved.reveal() == "from-the-store"


async def test_an_empty_slot_falls_through_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing stored is a normal state during setup, not a failure."""
    from engine.activities.nodes import StoreThenEnvBroker

    app_id = await _register()
    monkeypatch.setenv("KUWARDEN_SCM_TOKEN", "from-the-environment")
    monkeypatch.setenv("KUWARDEN_SECRET_KEY", KEY)

    resolved = await StoreThenEnvBroker(app_id).resolve(
        CredentialRequest(kind=CredentialKind.SCM_READ, realm="github.com/acme")
    )
    assert resolved.reveal() == "from-the-environment"


async def test_an_unopenable_credential_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong master key is a misconfiguration, not an absence.

    Falling through to the environment here would quietly run with a different credential
    than the one the operator stored, and the stored one would look fine in the UI.
    """
    from engine.activities.nodes import StoreThenEnvBroker

    app_id = await _register()
    await EncryptedPostgresStore(app_id, master_key=KEY).put(
        app_id, CredentialKind.SCM_READ, Secret("stored-under-the-first-key")
    )

    monkeypatch.setenv("KUWARDEN_SCM_TOKEN", "from-the-environment")
    monkeypatch.setenv("KUWARDEN_SECRET_KEY", OTHER_KEY)

    with pytest.raises(SecretKeyError):
        await StoreThenEnvBroker(app_id).resolve(
            CredentialRequest(kind=CredentialKind.SCM_READ, realm="github.com/acme")
        )


async def test_neither_source_names_both_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message has to say where it looked, or setup is guesswork."""
    from engine.activities.nodes import StoreThenEnvBroker

    app_id = await _register()
    monkeypatch.delenv("KUWARDEN_SCM_TOKEN", raising=False)
    monkeypatch.setenv("KUWARDEN_SECRET_KEY", KEY)

    with pytest.raises(PolicyDenied, match="Workbench.*environment"):
        await StoreThenEnvBroker(app_id).resolve(
            CredentialRequest(kind=CredentialKind.SCM_READ, realm="github.com/acme")
        )
