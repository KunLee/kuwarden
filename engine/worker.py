"""Worker entry point.

Runs on the host under `uv run`, not in a container — see compose.yaml. Killing this process
mid-run is a supported operation and is one of the things the walking skeleton exists to
prove: the run resumes where it stopped when a worker comes back.
"""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from engine.activities.audit import record_event, record_run_ended, record_run_started
from engine.activities.nodes import run_node
from engine.flows.delivery import DeliveryFlow

TASK_QUEUE = "kuwarden-delivery"


def target() -> str:
    return os.environ.get("KUWARDEN_TEMPORAL_TARGET", "127.0.0.1:7233")


def namespace() -> str:
    return os.environ.get("KUWARDEN_TEMPORAL_NAMESPACE", "kuwarden")


async def main() -> None:
    logging.basicConfig(level=os.environ.get("KUWARDEN_LOG_LEVEL", "INFO"))
    client = await Client.connect(target(), namespace=namespace())
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DeliveryFlow],
        activities=[run_node, record_run_started, record_event, record_run_ended],
    )
    logging.info("worker ready on %s/%s queue=%s", target(), namespace(), TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
