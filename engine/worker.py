"""Worker entry point.

Runs on the host under `uv run`, not in a container — see compose.yaml. Killing this process
mid-run is a supported operation and is one of the things the walking skeleton exists to
prove: the run resumes where it stopped when a worker comes back.

**The worker loads `kuwarden.yaml` at startup**, and refuses to start without one. Nodes
resolve their configuration from it, so a worker that starts without it accepts work it
cannot perform and fails at the first node — several minutes and one confusing traceback
later. Failing at startup is the same information, delivered where it is actionable.

**One worker currently serves one application's configuration.** That is a real limitation,
not a design: `kuwarden.yaml` belongs to the application's own repository, so the eventual
shape is reading it per run from the repo the run is about. Until then, run one worker per
application, pointed at that application's config with `KUWARDEN_CONFIG`. Credentials are
already per-application — those come from the encrypted store keyed by `app_id`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from engine.activities import ALL
from engine.activities.nodes import RUNTIME
from engine.build_id import build_id
from engine.config import ConfigError, load
from engine.devenv import load_dotenv
from engine.flows.delivery import DeliveryFlow

TASK_QUEUE = "kuwarden-delivery"

#: Where the application's configuration lives. A path rather than a directory scan, so a
#: worker started in the wrong directory says so instead of silently finding nothing.
CONFIG_VARIABLE = "KUWARDEN_CONFIG"
DEFAULT_CONFIG = "kuwarden.yaml"


def target() -> str:
    return os.environ.get("KUWARDEN_TEMPORAL_TARGET", "127.0.0.1:7233")


def namespace() -> str:
    return os.environ.get("KUWARDEN_TEMPORAL_NAMESPACE", "kuwarden")


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_VARIABLE, DEFAULT_CONFIG))


#: How much this worker will do at once.
#:
#: Bounded by default rather than unbounded, for the same reason the sandbox is: an agent loop
#: will otherwise consume the machine it runs on. Nothing else in the system caps this — one
#: task queue serves every application, and with auto-merge enabled an unbounded worker is an
#: unbounded number of unattended merges.
#:
#: **This is backpressure, not rejection.** Temporal is a durable queue: work beyond the cap
#: waits its turn and still runs, with a complete audit trail. Nothing is dropped, so the
#: conservative default costs throughput and never a ticket.
#:
#: `ACTIVITIES` is the one that matters. Each concurrent activity can be a podman container
#: plus a model call — at the 2 GiB sandbox limit, four of them is 8 GiB before the host's own
#: needs. Workflow tasks are cheap by comparison: they replay decisions and touch nothing
#: expensive, so throttling them hard only delays every run for no saving.
MAX_ACTIVITIES_VARIABLE = "KUWARDEN_MAX_CONCURRENT_ACTIVITIES"
MAX_WORKFLOW_TASKS_VARIABLE = "KUWARDEN_MAX_CONCURRENT_WORKFLOW_TASKS"
DEFAULT_MAX_ACTIVITIES = 4
DEFAULT_MAX_WORKFLOW_TASKS = 40


def _positive_int(variable: str, default: int) -> int:
    """Read a positive integer from the environment, or refuse to start.

    A malformed limit is not defaulted quietly. Someone typing `KUWARDEN_MAX_CONCURRENT_
    ACTIVITIES=four` has said what they want; silently running unbounded-ish at the default
    would leave them believing a cap was applied that was not — the same class of error as a
    sandbox reporting a limit it does not enforce.
    """
    raw = os.environ.get(variable)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{variable} must be a positive integer, got {raw!r}") from None
    if value < 1:
        raise ConfigError(f"{variable} must be at least 1, got {value}")
    return value


def limits() -> tuple[int, int]:
    """(activities, workflow tasks) this worker will run concurrently."""
    return (
        _positive_int(MAX_ACTIVITIES_VARIABLE, DEFAULT_MAX_ACTIVITIES),
        _positive_int(MAX_WORKFLOW_TASKS_VARIABLE, DEFAULT_MAX_WORKFLOW_TASKS),
    )


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("KUWARDEN_LOG_LEVEL", "INFO"))

    # Frozen here, before anything can reload a module, and reported so a worker running older
    # code than the repository can be seen from outside. On 2026-08-31 one could not be: it
    # polled, accepted every task and failed every one on a stale import, and the three hours
    # spent finding that are what this line costs to prevent.
    logging.info("worker build %s", build_id())

    # No longer required. Configuration is resolved per run from the `app_config` table
    # (ADR 0008), so a worker serving several applications has no business demanding one
    # application's file at startup. A file that *is* present becomes the fallback for
    # applications with nothing stored yet — which is how a single-application deployment
    # keeps working while it migrates.
    #
    # Credentials are deliberately not passed here either. The broker is chosen per run from
    # the `app_id` the trigger supplied — see `NodeRuntime._broker_for`.
    path = config_path()
    if path.is_file():
        config = load(path)
        RUNTIME.configure(config)
        logging.info("fallback configuration %s loaded for %r", path, config.name)
    else:
        RUNTIME.configure()
        logging.info(
            "no %s file; every application must have configuration stored in the Workbench",
            CONFIG_VARIABLE,
        )

    max_activities, max_workflow_tasks = limits()

    client = await Client.connect(target(), namespace=namespace())
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DeliveryFlow],
        activities=ALL,
        max_concurrent_activities=max_activities,
        max_concurrent_workflow_tasks=max_workflow_tasks,
    )
    # Logged because the ceiling of a deployment is this number times the number of workers
    # polling the queue, and an operator cannot work that out from a running process
    # otherwise.
    logging.info(
        "worker ready on %s/%s queue=%s activities<=%d workflow_tasks<=%d",
        target(),
        namespace(),
        TASK_QUEUE,
        max_activities,
        max_workflow_tasks,
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
