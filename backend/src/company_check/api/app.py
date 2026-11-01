"""FastAPI app: REST commands in, SSE event stream out.

    uv run uvicorn company_check.api.app:app --reload

Commands (answer, feedback, pause, resume, retry) are recorded in `commands` and forwarded to the
check's workflow as signals. Both layers dedupe on the client's command_id, so a double
click or a retried request is harmless.
"""

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from temporalio.client import WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode

from company_check.api.schemas import (
    AnswerIn,
    CheckOut,
    CommandAccepted,
    ControlIn,
    CreateCheck,
    FeedbackIn,
)
from company_check.config import get_settings
from company_check.db import get_session
from company_check.event_store import channel, read_events
from company_check.models import Check, Command
from company_check.pg import close_pool, get_pool
from company_check.temporal_client import get_temporal_client
from company_check.worker.types import AnswerSignal, CheckInput, ControlSignal, FeedbackSignal
from company_check.worker.workflows import CheckWorkflow

SSE_PING_S = 15


@asynccontextmanager
async def lifespan(_: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Live Company Check", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def workflow_id(check_id: str) -> str:
    return f"check-{check_id}"


async def _get_check(session: AsyncSession, check_id: str) -> Check:
    try:
        uuid.UUID(check_id)
    except ValueError:
        raise HTTPException(404, "check not found") from None
    check = await session.get(Check, check_id)
    if check is None:
        raise HTTPException(404, "check not found")
    return check


async def _handle(check_id: str) -> WorkflowHandle:
    return (await get_temporal_client()).get_workflow_handle(workflow_id(check_id))


async def _signal(check_id: str, signal, arg) -> None:
    try:
        await (await _handle(check_id)).signal(signal, arg)
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:  # workflow finished
            raise HTTPException(409, "this check is no longer running") from None
        raise


async def _record_command(
    session: AsyncSession, check_id: str, command_id: str, kind: str, payload: dict[str, Any]
) -> bool:
    """Store the command; returns False if this command_id was already seen."""
    stmt = (
        insert(Command)
        .values(id=command_id, check_id=check_id, kind=kind, payload=payload)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(Command.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    await session.commit()
    return inserted is not None


# --- health & checks ---------------------------------------------------------


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("select 1"))
    client = await get_temporal_client()
    await client.service_client.check_health()
    return {"database": "ok", "temporal": "ok"}


@app.post("/checks", response_model=CheckOut, status_code=201)
async def create_check(body: CreateCheck, session: AsyncSession = Depends(get_session)) -> Check:
    check = Check(id=str(uuid.uuid4()), input=body.query.strip(), status="starting")
    session.add(check)
    await session.commit()
    client = await get_temporal_client()
    await client.start_workflow(
        CheckWorkflow.run,
        CheckInput(check_id=check.id, query=check.input),
        id=workflow_id(check.id),
        task_queue=get_settings().temporal_task_queue,
    )
    await session.refresh(check)
    return check


@app.get("/checks", response_model=list[CheckOut])
async def list_checks(session: AsyncSession = Depends(get_session)) -> list[Check]:
    rows = await session.scalars(select(Check).order_by(Check.created_at.desc()).limit(50))
    return list(rows)


@app.get("/checks/{check_id}", response_model=CheckOut)
async def get_check(check_id: str, session: AsyncSession = Depends(get_session)) -> Check:
    return await _get_check(session, check_id)


# --- commands ----------------------------------------------------------------


@app.post("/checks/{check_id}/answers", response_model=CommandAccepted)
async def answer(
    check_id: str, body: AnswerIn, session: AsyncSession = Depends(get_session)
) -> CommandAccepted:
    await _get_check(session, check_id)
    first = await _record_command(
        session, check_id, body.command_id, "answer", body.model_dump(exclude={"command_id"})
    )
    # Signal even on a repeat: if the first attempt failed after recording, this delivers it;
    # the workflow ignores command_ids it has already processed.
    await _signal(
        check_id,
        CheckWorkflow.answer,
        AnswerSignal(body.command_id, body.question_id, body.value),
    )
    return CommandAccepted(command_id=body.command_id, duplicate=not first)


@app.post("/checks/{check_id}/feedback", response_model=CommandAccepted)
async def feedback(
    check_id: str, body: FeedbackIn, session: AsyncSession = Depends(get_session)
) -> CommandAccepted:
    """Feedback can be sent at any time; the workflow queues it until the agent is free."""
    await _get_check(session, check_id)
    payload = body.model_dump(exclude={"command_id"})
    first = await _record_command(session, check_id, body.command_id, "feedback", payload)
    await _signal(
        check_id,
        CheckWorkflow.feedback,
        FeedbackSignal(body.command_id, body.text.strip(), payload["target"]),
    )
    return CommandAccepted(command_id=body.command_id, duplicate=not first)


async def _control(check_id: str, kind: str, body: ControlIn, session: AsyncSession, signal):
    await _get_check(session, check_id)
    first = await _record_command(session, check_id, body.command_id, kind, {})
    await _signal(check_id, signal, ControlSignal(body.command_id))
    return CommandAccepted(command_id=body.command_id, duplicate=not first)


@app.post("/checks/{check_id}/pause", response_model=CommandAccepted)
async def pause(check_id: str, body: ControlIn, session: AsyncSession = Depends(get_session)):
    return await _control(check_id, "pause", body, session, CheckWorkflow.pause)


@app.post("/checks/{check_id}/resume", response_model=CommandAccepted)
async def resume(check_id: str, body: ControlIn, session: AsyncSession = Depends(get_session)):
    return await _control(check_id, "resume", body, session, CheckWorkflow.resume)


@app.post("/checks/{check_id}/retry", response_model=CommandAccepted)
async def retry(check_id: str, body: ControlIn, session: AsyncSession = Depends(get_session)):
    return await _control(check_id, "retry", body, session, CheckWorkflow.retry)


# --- event stream ------------------------------------------------------------


def _sse(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["seq"]),
        "data": json.dumps(
            {
                "seq": row["seq"],
                "type": row["type"],
                "payload": row["payload"],
                "ts": row["created_at"].isoformat(),
            }
        ),
    }


@app.get("/checks/{check_id}/events")
async def events(
    check_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Server-Sent Events: replay everything after `Last-Event-ID` (or ?after=), then live.

    LISTEN starts *before* the replay query, so nothing committed in between is missed;
    tracking the last seq sent means nothing is sent twice.
    """
    await _get_check(session, check_id)
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    pool = await get_pool()

    async def stream():
        last = start
        # A dedicated connection per stream for LISTEN (simple; fine at this scale).
        async with await psycopg.AsyncConnection.connect(
            get_settings().psycopg_dsn, autocommit=True, row_factory=dict_row
        ) as conn:
            await conn.execute(f'LISTEN "{channel(check_id)}"')
            while True:
                for row in await read_events(pool, check_id, after=last):
                    last = row["seq"]
                    yield _sse(row)
                if await request.is_disconnected():
                    return
                # Wake on the next NOTIFY, or every few seconds as a safety net.
                async for _ in conn.notifies(timeout=5, stop_after=1):
                    pass

    return EventSourceResponse(stream(), ping=SSE_PING_S)
