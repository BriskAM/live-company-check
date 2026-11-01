"""Temporal worker process: runs workflows and activities.

    uv run python -m company_check.worker.main
"""

import asyncio
import logging

from temporalio.worker import Worker

from company_check.config import get_settings
from company_check.temporal_client import get_temporal_client
from company_check.worker.workflows import CheckWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    client = await get_temporal_client()
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[CheckWorkflow],
        activities=[],
    )
    logging.info("worker listening on task queue %r", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
