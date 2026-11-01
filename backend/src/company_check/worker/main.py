"""Temporal worker process: runs workflows and activities.

uv run python -m company_check.worker.main
"""

import asyncio
import logging
import signal
from datetime import timedelta

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from temporalio.worker import Worker

from company_check.config import get_settings
from company_check.pg import close_pool, get_pool
from company_check.temporal_client import get_temporal_client
from company_check.worker.activities import emit_event, run_agent
from company_check.worker.workflows import CheckWorkflow

log = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    pool = await get_pool()
    await AsyncPostgresSaver(pool).setup()  # create/migrate LangGraph checkpoint tables
    client = await get_temporal_client()
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[CheckWorkflow],
        activities=[run_agent, emit_event],
        # By default heartbeats are batched to 80% of the heartbeat timeout, and a pause
        # (activity cancellation) is only delivered in a heartbeat response. Send them every
        # second so pausing stops the agent within ~1s.
        max_heartbeat_throttle_interval=timedelta(seconds=1),
        default_heartbeat_throttle_interval=timedelta(seconds=1),
        # On shutdown, running agents are cancelled: they save the text streamed so far and
        # Temporal retries them on the next worker, which continues from the checkpoint.
        graceful_shutdown_timeout=timedelta(seconds=5),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):  # docker stop sends SIGTERM
        loop.add_signal_handler(sig, stop.set)
    log.info("worker listening on task queue %r", settings.temporal_task_queue)
    try:
        async with worker:
            await stop.wait()
            log.info("shutting down")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
