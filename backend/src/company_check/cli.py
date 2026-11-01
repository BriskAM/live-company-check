"""Run a check from the terminal (no Temporal/UI) — for developing and debugging the agent.

    uv run python -m company_check.cli "Brightline Logistics"
    uv run python -m company_check.cli "Brightline Logistics" --answer cmp_001

Questions are answered from --answer values in order, then interactively. For single-choice
questions type the option id; for multi-choice, ids separated by commas.
"""

import argparse
import asyncio
import json
import uuid

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from company_check.agent.events import ConsoleSink
from company_check.agent.graph import build_graph
from company_check.agent.state import AgentContext
from company_check.config import get_settings
from company_check.db import engine


def _match(token: str, options: list[dict]) -> str:
    """Option id for a token: exact id, else first label containing it (case-insensitive)."""
    for o in options:
        if o["id"] == token:
            return o["id"]
    for o in options:
        if token.lower() in o["label"].lower():
            return o["id"]
    return token


def _answer(question: dict, scripted: list[str]):
    raw = scripted.pop(0) if scripted else input("answer> ").strip()
    if question["kind"] == "text":
        answer: object = raw
    elif question["kind"] == "multi":
        answer = [_match(t.strip(), question["options"]) for t in raw.split(",") if t.strip()]
    else:
        answer = _match(raw, question["options"])
    print(f"  ↳ {answer}")
    return answer


async def run(query: str, answers: list[str], thread_id: str) -> None:
    async with AsyncPostgresSaver.from_conn_string(get_settings().psycopg_dsn) as saver:
        await saver.setup()
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": thread_id}}
        ctx = AgentContext(sink=ConsoleSink())

        payload: object = {"query": query}
        while True:
            result = await graph.ainvoke(payload, config, context=ctx)
            pending = result.get("__interrupt__")
            if not pending:
                break
            # Several questions can be pending at once; answer each by interrupt id.
            payload = Command(resume={i.id: _answer(i.value, answers) for i in pending})

        print("\n=== final state ===")
        print(json.dumps({k: result.get(k) for k in ("concerns", "recommendation")}, indent=1))
    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--answer", action="append", default=[])
    p.add_argument("--thread", default=None)
    a = p.parse_args()
    asyncio.run(run(a.query, a.answer, a.thread or str(uuid.uuid4())))


if __name__ == "__main__":
    main()
