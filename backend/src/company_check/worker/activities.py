"""Activities: everything non-deterministic (DB, LLM) runs here, never in workflow code."""

import asyncio
import contextlib
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from temporalio import activity
from temporalio.exceptions import ApplicationError

from company_check.agent.graph import build_graph
from company_check.agent.state import AgentContext
from company_check.event_store import PostgresSink, append_event
from company_check.pg import get_pool
from company_check.worker.types import EmitInput, RunAgentInput, RunAgentResult

HEARTBEAT_EVERY_S = 1

# LLM errors that retrying cannot fix (bad key, no billing, no access). Overloads (503),
# rate limits (429) and timeouts are left retryable.
PERMANENT_LLM_ERRORS = (
    " 401 ",
    " 402 ",
    " 403 ",
    "API_KEY_INVALID",
    "PERMISSION_DENIED",
    "credits are depleted",
)


def _is_permanent(e: Exception) -> bool:
    text = f" {e} "
    return any(marker in text for marker in PERMANENT_LLM_ERRORS)


async def _heartbeat() -> None:
    # Heartbeats let Temporal (a) notice a dead worker quickly and retry elsewhere and
    # (b) deliver cancellation (pause) into this activity. Cancellation only arrives in a
    # heartbeat response, so the worker's heartbeat throttle (main.py) bounds pause latency.
    while True:
        activity.heartbeat()
        await asyncio.sleep(HEARTBEAT_EVERY_S)


def _graph_input(snapshot, inp: RunAgentInput) -> tuple[Any, list[dict[str, Any]]]:
    """Decide how to (re)enter the graph from its last checkpoint.

    Returns (input, unanswered_questions). Input None means "continue where it stopped".
    """
    if snapshot.created_at is None:  # never ran
        return {"query": inp.query}, []
    if snapshot.interrupts:
        pending = [i for i in snapshot.interrupts if i.value["question_id"] not in inp.answers]
        if pending:
            return None, [i.value for i in pending]
        return Command(
            resume={i.id: inp.answers[i.value["question_id"]] for i in snapshot.interrupts}
        ), []
    return None, []


@activity.defn
async def run_agent(inp: RunAgentInput) -> RunAgentResult:
    """Run the agent graph until it finishes or needs the reviewer.

    Safe to retry and to cancel at any point: LangGraph checkpoints after every node, so the
    next attempt continues from the last completed node.
    """
    pool = await get_pool()
    graph = build_graph(AsyncPostgresSaver(pool))
    config = {"configurable": {"thread_id": inp.check_id}}
    sink = PostgresSink(pool, inp.check_id)

    hb = asyncio.create_task(_heartbeat())
    try:
        snapshot = await graph.aget_state(config)
        payload, unanswered = _graph_input(snapshot, inp)
        if unanswered:
            return RunAgentResult(status="waiting", questions=unanswered)
        if snapshot.created_at is not None and not snapshot.next and not snapshot.interrupts:
            return RunAgentResult(status="done")

        result = await graph.ainvoke(payload, config, context=AgentContext(sink=sink))
        pending = result.get("__interrupt__") or []
        if pending:
            return RunAgentResult(status="waiting", questions=[i.value for i in pending])
        return RunAgentResult(status="done")
    except asyncio.CancelledError:
        # Paused (or worker shutting down): persist the text streamed so far, then stop.
        with contextlib.suppress(Exception):
            await sink.flush()
        raise
    except Exception as e:
        with contextlib.suppress(Exception):
            await sink.flush()
        if _is_permanent(e):
            # Fail fast: the workflow shows the error and waits for the reviewer to retry
            # (e.g. after fixing the key) instead of burning retries.
            raise ApplicationError(
                f"LLM configuration problem: {e}", type="LLMConfigError", non_retryable=True
            ) from e
        raise
    finally:
        hb.cancel()


@activity.defn
async def emit_event(inp: EmitInput) -> None:
    """Workflow-level events (status changes, accepted answers)."""
    await append_event(await get_pool(), inp.check_id, inp.type, inp.payload, inp.key)
