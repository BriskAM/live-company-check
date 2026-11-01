"""Append-only check event log + the durable EventSink used by the worker.

append_event() is the only writer. In one transaction it:
  1. locks the check row (serialises writers for that check),
  2. skips the insert if an event with the same idempotency key exists,
  3. takes the next seq, inserts, and NOTIFYs `check_<id>` (delivered on commit).

Readers (the SSE endpoint) replay `seq > last_seen` and then wait for notifications.
"""

import time
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

# Streamed text is written in batches: at most one delta row per message every FLUSH_INTERVAL.
FLUSH_INTERVAL_S = 0.1


def channel(check_id: str) -> str:
    return f"check_{check_id.replace('-', '')}"


async def append_event(
    pool: AsyncConnectionPool,
    check_id: str,
    type: str,
    payload: dict[str, Any],
    key: str | None = None,
) -> int | None:
    """Append one event. Returns its seq, or None if `key` was already recorded."""
    async with pool.connection() as conn, conn.transaction():
        row = await (
            await conn.execute("SELECT last_seq FROM checks WHERE id = %s FOR UPDATE", (check_id,))
        ).fetchone()
        if row is None:
            raise LookupError(f"check {check_id} not found")
        if key is not None:
            dup = await (
                await conn.execute(
                    "SELECT 1 FROM check_events WHERE check_id = %s AND key = %s", (check_id, key)
                )
            ).fetchone()
            if dup:
                return None
        seq = row["last_seq"] + 1
        status = payload.get("status") if type == "status" else None
        await conn.execute(
            "UPDATE checks SET last_seq = %s, updated_at = now(),"
            " status = COALESCE(%s, status) WHERE id = %s",
            (seq, status, check_id),
        )
        await conn.execute(
            "INSERT INTO check_events (check_id, seq, type, payload, key)"
            " VALUES (%s, %s, %s, %s, %s)",
            (check_id, seq, type, Jsonb(payload), key),
        )
        await conn.execute("SELECT pg_notify(%s, %s)", (channel(check_id), str(seq)))
        return seq


async def read_events(
    pool: AsyncConnectionPool, check_id: str, after: int = 0, limit: int = 1000
) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT seq, type, payload, created_at FROM check_events"
            " WHERE check_id = %s AND seq > %s ORDER BY seq LIMIT %s",
            (check_id, after, limit),
        )
        return await cur.fetchall()


async def message_progress(
    pool: AsyncConnectionPool, check_id: str, message_id: str
) -> tuple[str, bool]:
    """Text already streamed for a message in its latest attempt, and whether it finished.

    Used to continue a message after a pause or a worker crash instead of rewriting it.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            WITH latest AS (
                SELECT COALESCE(max(seq), 0) AS start FROM check_events
                WHERE check_id = %(c)s AND type = 'message_start'
                  AND payload->>'message_id' = %(m)s
            )
            SELECT type, payload->>'text' AS text FROM check_events, latest
            WHERE check_id = %(c)s AND seq > latest.start
              AND type IN ('message_delta', 'message_end')
              AND payload->>'message_id' = %(m)s
            ORDER BY seq
            """,
            {"c": check_id, "m": message_id},
        )
        rows = await cur.fetchall()
    text = "".join(r["text"] for r in rows if r["type"] == "message_delta")
    return text, any(r["type"] == "message_end" for r in rows)


class PostgresSink:
    """EventSink that writes to check_events.

    - Text deltas are buffered and flushed every FLUSH_INTERVAL_S (and before any other event,
      so ordering is preserved).
    - The first delta of a message emits `message_start`. A retried step creates a new sink and
      re-emits it, which tells the UI to discard any half-streamed text for that message id.
    """

    def __init__(self, pool: AsyncConnectionPool, check_id: str) -> None:
        self.pool = pool
        self.check_id = check_id
        self._buffers: dict[str, list[str]] = {}
        self._started: set[str] = set()
        self._last_flush = time.monotonic()

    async def emit(self, type: str, payload: dict[str, Any], key: str | None = None) -> None:
        await self.flush()
        await append_event(self.pool, self.check_id, type, payload, key)

    async def delta(self, message_id: str, text: str) -> None:
        if message_id not in self._started:
            self._started.add(message_id)
            await self.emit("message_start", {"message_id": message_id})
        self._buffers.setdefault(message_id, []).append(text)
        if time.monotonic() - self._last_flush >= FLUSH_INTERVAL_S:
            await self.flush()

    async def end_message(self, message_id: str, **payload: Any) -> None:
        await self.emit("message_end", {"message_id": message_id, **payload})

    async def resume_message(self, message_id: str) -> tuple[str, bool]:
        """Return (text so far, finished) and continue appending to it, without a reset."""
        await self.flush()
        text, finished = await message_progress(self.pool, self.check_id, message_id)
        if text:
            self._started.add(message_id)
        return text, finished

    async def restart_message(self, message_id: str) -> None:
        """Discard what was streamed for this message (UI clears it) and start over."""
        await self.flush()
        self._started.add(message_id)
        await self.emit("message_start", {"message_id": message_id})

    async def flush(self) -> None:
        self._last_flush = time.monotonic()
        buffers, self._buffers = self._buffers, {}
        for message_id, parts in buffers.items():
            if parts:
                await append_event(
                    self.pool,
                    self.check_id,
                    "message_delta",
                    {"message_id": message_id, "text": "".join(parts)},
                )
