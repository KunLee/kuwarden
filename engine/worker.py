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


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("KUWARDEN_LOG_LEVEL", "INFO"))

    path = config_path()
    if not path.is_file():
        raise ConfigError(
            f"no configuration at {path}. The worker needs the application's kuwarden.yaml; "
            f"point {CONFIG_VARIABLE} at it, or run from a directory that contains one."
        )
    config = load(path)
    # Credentials are deliberately not passed here. The broker is chosen per run from the
    # `app_id` the trigger supplied, so the Workbench's encrypted store is what resolves them
    # — see `NodeRuntime._broker_for`.
    RUNTIME.configure(config)
    logging.info("loaded %s for application %r", path, config.name)

    client = await Client.connect(target(), namespace=namespace())
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DeliveryFlow],
        activities=ALL,
    )
    logging.info("worker ready on %s/%s queue=%s", target(), namespace(), TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
