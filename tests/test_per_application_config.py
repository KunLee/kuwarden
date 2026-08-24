"""One worker, many applications — configuration resolved per run.

The failure this closes: `RUNTIME.context(app_id)` resolved credentials per application and
handed back the worker's single `AppConfig` regardless, so the second registered application
ran against the first one's repository list, tiering rules and merge policy.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from engine import config_store
from engine.db import connect
from engine.errors import PolicyDenied
from tests.conftest import KUWARDEN_YAML


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    config_store.forget()
    yield
    config_store.forget()


async def _register(name: str, model: str = "gated_deployment") -> uuid.UUID:
    app_id = uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,$4)",
            app_id, name, f"https://example.invalid/{name}", model,
        )
    return app_id


async def _drop(app_id: uuid.UUID) -> None:
    async with connect() as conn:
        await conn.execute("DELETE FROM app_config WHERE app_id = $1", app_id)
        await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)


async def _store(app_id: uuid.UUID, yaml: str) -> None:
    """Write configuration the way `PUT /config` does — including dropping the cache.

    The endpoint calls `config_store.forget` after writing, which is what makes an edit apply
    at once rather than within the resolver's TTL. A test that wrote to the table directly
    would be asserting the behaviour of a path no operator uses.
    """
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_config (app_id, yaml, updated_by) VALUES ($1,$2,'test') "
            "ON CONFLICT (app_id) DO UPDATE SET yaml = EXCLUDED.yaml, updated_at = now()",
            app_id, yaml,
        )
    config_store.forget(app_id)


async def test_two_applications_resolve_to_their_own_configuration() -> None:
    """The whole point. Before this, both answered with the worker's single file."""
    first = await _register("alpha-app")
    second = await _register("beta-app")
    try:
        await _store(first, KUWARDEN_YAML.replace("name: payments-service", "name: alpha-app"))
        await _store(second, KUWARDEN_YAML.replace("name: payments-service", "name: beta-app"))

        assert (await config_store.resolve(first)).name == "alpha-app"
        assert (await config_store.resolve(second)).name == "beta-app"
    finally:
        await _drop(first)
        await _drop(second)


async def test_an_edit_takes_effect_without_restarting_the_worker() -> None:
    """Cached on the row's timestamp, so a change lands on the next run."""
    app_id = await _register("mutable-app")
    try:
        await _store(app_id, KUWARDEN_YAML.replace("name: payments-service", "name: before"))
        assert (await config_store.resolve(app_id)).name == "before"

        await _store(app_id, KUWARDEN_YAML.replace("name: payments-service", "name: after"))
        assert (await config_store.resolve(app_id)).name == "after"
    finally:
        await _drop(app_id)


async def test_a_control_point_disagreement_is_refused_not_resolved() -> None:
    """`integration_model` is declared in two places, and the registry is authoritative.

    Silently preferring one would mean a run governed by a model nobody declared through the
    endpoint that records the change. Two declarations of the same fact is how this repository
    has already been bitten twice.
    """
    app_id = await _register("mismatched-app", model="gated_merge")
    try:
        await _store(app_id, KUWARDEN_YAML)  # the shared fixture declares gated_deployment
        with pytest.raises(PolicyDenied, match="registered with control point"):
            await config_store.resolve(app_id)
    finally:
        await _drop(app_id)


async def test_configuration_that_does_not_parse_refuses_the_run_by_name() -> None:
    app_id = await _register("broken-app")
    try:
        await _store(app_id, "version: 1\nthis is not: [a valid kuwarden.yaml")
        with pytest.raises(PolicyDenied, match="does not parse"):
            await config_store.resolve(app_id)
    finally:
        await _drop(app_id)


async def test_an_unregistered_application_is_refused() -> None:
    with pytest.raises(PolicyDenied, match="no application"):
        await config_store.resolve(uuid.uuid4())


async def test_an_application_with_no_stored_configuration_falls_back_to_the_file() -> None:
    """Existing single-application deployments must not break on upgrade.

    The fallback is safe because Triage's application guard still runs: if the file is for a
    different application, the run is refused there with a sentence naming both.
    """
    app_id = await _register("unstored-app")
    try:
        config = await config_store.resolve(app_id)
        # Whatever the worker's own file says — the point is that it resolved rather than
        # raising, and that it is not this application's own configuration.
        assert config.name != "unstored-app"
    finally:
        await _drop(app_id)


async def test_an_out_of_band_edit_applies_within_the_resolvers_ttl() -> None:
    """The cost of not querying the database on every node of every run.

    An edit made through the Workbench is immediate, because the endpoint drops the cache. One
    made straight against the table — a migration, a support script — is picked up when the
    entry expires. Recorded as a property rather than left for someone to discover.
    """
    app_id = await _register("ttl-app")
    try:
        await _store(app_id, KUWARDEN_YAML.replace("name: payments-service", "name: first"))
        assert (await config_store.resolve(app_id)).name == "first"

        async with connect() as conn:
            await conn.execute(
                "UPDATE app_config SET yaml = $2, updated_at = now() WHERE app_id = $1",
                app_id,
                KUWARDEN_YAML.replace("name: payments-service", "name: second"),
            )

        # Still the cached answer: no cache drop accompanied this write.
        assert (await config_store.resolve(app_id)).name == "first"

        config_store.forget(app_id)
        assert (await config_store.resolve(app_id)).name == "second"
    finally:
        await _drop(app_id)


async def test_a_worker_with_no_file_refuses_by_name_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """"Registered but not configured" must be a sentence, not an inheritance.

    A worker serving several applications has no single file that could be right for all of
    them, so falling back to whichever one is on disk is not a default — it is a wrong answer
    that happens to be correct for one tenant.
    """
    # A worker that was started without a file. The developer checkout has one sitting in the
    # working directory, so the variable has to be pointed away from it.
    monkeypatch.setenv("KUWARDEN_CONFIG", str(tmp_path) + "/absent-kuwarden.yaml")
    app_id = await _register("unconfigured-app")
    try:
        with pytest.raises(PolicyDenied, match="unconfigured-app"):
            # No stored row, and no fallback offered — what a multi-application worker looks
            # like once it stops carrying one application's file.
            await config_store.resolve(app_id, fallback=None)
    finally:
        await _drop(app_id)


async def test_the_sandbox_is_built_from_the_configuration_that_governs_the_run() -> None:
    """`require_full_isolation` is per application, and used to be bound once at startup.

    Sharing one sandbox across applications meant one asking for strict isolation silently ran
    under another's relaxed setting — a bound reported but not applied, which is the failure
    ADR 0005 exists to prevent.
    """
    from dataclasses import replace

    from engine.activities.nodes import RUNTIME
    from engine.config import parse
    from engine.sandbox.podman import PodmanSandbox

    base = parse(KUWARDEN_YAML)
    relaxed = replace(base, sandbox=replace(base.sandbox, require_full_isolation=False))
    strict = replace(base, sandbox=replace(base.sandbox, require_full_isolation=True))

    # Configured with the relaxed one, as a worker started from a file would be.
    RUNTIME.configure(relaxed)
    try:
        loose = RUNTIME.context(config=relaxed).sandbox
        tight = RUNTIME.context(config=strict).sandbox
        assert isinstance(loose, PodmanSandbox)
        assert isinstance(tight, PodmanSandbox)
        assert loose is not tight, "each run must get a sandbox built from its own config"
        assert tight._require_full_isolation is True
        assert loose._require_full_isolation is False
    finally:
        RUNTIME.configure(base)
