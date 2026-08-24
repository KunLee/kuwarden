"""Where an application's configuration comes from, and which one governs a run.

The worker used to load one `kuwarden.yaml` at startup. That made a worker a single-tenant
process by construction: `RUNTIME.context(app_id)` resolved *credentials* per application and
handed back the same `AppConfig` regardless, so a run for the second registered application
read the first one's repository list, tiering rules and merge policy while holding the second
one's tokens.

Resolution order, and both halves matter:

1. **`app_config` in the database**, keyed on the application. This is the multi-tenant path.
2. **The worker's `KUWARDEN_CONFIG` file**, when that application has no stored row. Kept so
   an existing single-application deployment does not break the moment this ships, and so a
   deployment can migrate one application at a time. The application guard in Triage still
   applies on this path, which is what stops the fallback silently governing the wrong run.

`integration_model` is deliberately *not* taken from the YAML. It is the control point — ADR
0004 — and it already lives in `app_registry`, changeable only through an endpoint that
records the change in the append-only `app_changes` table. Two declarations of the same fact
is how this repository has been bitten before, so a disagreement is refused rather than
resolved by precedence.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from time import monotonic
from uuid import UUID

from engine.adapters.protocols import IntegrationModel
from engine.config import AppConfig, ConfigError, load, parse
from engine.db import connect
from engine.errors import PolicyDenied

log = logging.getLogger(__name__)

#: Resolved configurations, per application: (config, row timestamp, when this expires).
#:
#: Two costs are being avoided and they are different. Parsing YAML is cheap but not free;
#: *querying for the timestamp in order to know whether to re-parse* is a database round trip,
#: and `run_node` calls this on every node of every run. The TTL is what turns that into one
#: query per application per interval. The consequence is stated rather than hidden: an edit
#: made in the Workbench applies to runs starting more than `_TTL_SECONDS` later, not
#: instantly.
_CACHE: dict[UUID, tuple[AppConfig, str, float]] = {}

#: Short enough that an operator editing configuration does not wonder whether it saved.
_TTL_SECONDS = 10.0


def forget(app_id: UUID | None = None) -> None:
    """Drop cached configuration. For tests, and after a write, so an edit lands at once."""
    if app_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(app_id, None)


async def resolve(app_id: UUID | None, fallback: AppConfig | None = None) -> AppConfig:
    """The configuration that governs a run for `app_id`.

    `fallback` is what governs an application with no stored configuration — the worker's own
    startup config. Passed in rather than read here, because in a test that is the config the
    fixture injected, and reading the file directly would make every test that runs a flow
    silently execute against whatever `kuwarden.yaml` happens to sit in the developer's
    working directory.

    Raises `PolicyDenied` rather than falling back when a stored configuration exists but
    disagrees with the registry about the control point: proceeding would mean the run is
    governed by a model nobody declared through the audited path.
    """
    if app_id is None:
        # No application named. The file is all there is, and Triage's guard refuses the run
        # anyway — this exists so the failure is the guard's clear sentence rather than a
        # KeyError three frames deeper.
        return fallback or _from_file()

    now = monotonic()
    cached = _CACHE.get(app_id)
    if cached is not None and cached[2] > now:
        return cached[0]

    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT c.yaml, c.updated_at::text AS stamp, r.integration_model, r.name "
            "FROM app_registry r LEFT JOIN app_config c ON c.app_id = r.id "
            "WHERE r.id = $1",
            app_id,
        )

    if row is None:
        raise PolicyDenied(f"no application {app_id} is registered")
    if row["yaml"] is None:
        # Not an error: a deployment that has always run one worker per application has no
        # stored rows, and breaking it on upgrade would be a worse failure than the one this
        # module fixes.
        log.info("application %s has no stored configuration; using the worker's own", app_id)
        return fallback or _from_file(str(row["name"]))

    stamp = str(row["stamp"])
    # Re-parse only when the row actually changed. The TTL above decides how often we ask.
    if cached is not None and cached[1] == stamp:
        _CACHE[app_id] = (cached[0], stamp, now + _TTL_SECONDS)
        return cached[0]

    try:
        config = parse(str(row["yaml"]))
    except ConfigError as exc:
        raise PolicyDenied(
            f"the stored configuration for {row['name']!r} does not parse: {exc}. "
            "Fix it in the Workbench; runs for this application cannot start until it does."
        ) from None

    declared = IntegrationModel(str(row["integration_model"]))
    if config.integration_model is not declared:
        raise PolicyDenied(
            f"{row['name']!r} is registered with control point {declared.value!r} but its "
            f"stored kuwarden.yaml declares {config.integration_model.value!r}. The registry "
            "is authoritative — it is the one changed through an audited endpoint — so this "
            "is refused rather than silently resolved. Make them agree."
        )

    _CACHE[app_id] = (config, stamp, now + _TTL_SECONDS)
    return config


def _from_file(name: str | None = None) -> AppConfig:
    """The worker's own `KUWARDEN_CONFIG`, for applications with no stored configuration.

    Raises rather than returning something arbitrary when there is no file either. "Registered
    but not configured" is a state an operator can fix in one place; inheriting whichever
    application's file happened to be on the worker's disk is not.
    """
    from engine.worker import config_path

    path = config_path()
    if not path.is_file():
        raise PolicyDenied(
            f"no configuration stored for {name or 'this application'}, and this worker has "
            "no fallback file. Add it on the application's page in the Workbench."
        )
    return load(path)


def with_control_point(config: AppConfig, declared: IntegrationModel) -> AppConfig:
    """Return `config` governed by `declared`. Used where the registry is known to be right."""
    return replace(config, integration_model=declared)
