"""Clear every check so a demo starts from an empty list. Company data is kept.

    uv run python scripts/reset_demo.py          # asks first
    uv run python scripts/reset_demo.py --yes

Stops each check's workflow in Temporal, then deletes the checks with their events and
commands, and the agent's LangGraph checkpoints.
"""

import argparse
import asyncio

from temporalio.service import RPCError

from company_check.pg import close_pool, get_pool
from company_check.temporal_client import get_temporal_client

CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


async def main(yes: bool) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        ids = [r["id"] for r in await (await conn.execute("SELECT id FROM checks")).fetchall()]
    if not ids:
        print("No checks to clear.")
        return
    if not yes and input(f"Delete {len(ids)} check(s)? [y/N] ").strip().lower() != "y":
        return

    client = await get_temporal_client()
    stopped = 0
    for check_id in ids:
        try:
            await client.get_workflow_handle(f"check-{check_id}").terminate("demo reset")
            stopped += 1
        except RPCError:
            pass  # already closed

    async with pool.connection() as conn:
        exists = await (
            await conn.execute(
                "SELECT tablename FROM pg_tables WHERE tablename = ANY(%s)",
                [list(CHECKPOINT_TABLES)],
            )
        ).fetchall()
        for t in CHECKPOINT_TABLES:
            if any(r["tablename"] == t for r in exists):
                await conn.execute(
                    f"DELETE FROM {t} WHERE thread_id = ANY(%s)", [[str(i) for i in ids]]
                )
        await conn.execute("DELETE FROM checks")  # events and commands cascade
    await close_pool()
    print(f"Deleted {len(ids)} check(s); stopped {stopped} running workflow(s).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="don't ask")
    asyncio.run(main(ap.parse_args().yes))
