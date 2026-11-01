"""The company-check agent as a LangGraph state machine.

    resolve ──▶ gather ──▶ analyst ⇄ tools ──▶ report ──▶ END
       ▲  │
       └──┘ (asks for a better name when nothing matches)

- resolve / gather are deterministic code: they call the data tools directly (cheap, reliable).
- analyst is one LLM call per node run; tools executes its tool calls in a separate node.
  Splitting them matters for resume: `interrupt()` re-runs the node it is in, so a question
  asked inside `tools` never repeats the (already checkpointed) LLM call.
- Every node streams progress through the injected EventSink.
"""

import json
from datetime import date
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from company_check.agent import continuation, data
from company_check.agent.llm import chat_model
from company_check.agent.prompts import (
    ANALYST_SYSTEM,
    CONTINUE_REPORT,
    REPORT_SYSTEM,
    dossier,
    report_input,
)
from company_check.agent.state import (
    AgentContext,
    AskReviewer,
    CheckState,
    SearchNews,
    SubmitFindings,
)
from company_check.db import SessionLocal

# Guard against an analyst that never calls submit_findings.
MAX_ANALYST_TURNS = 8
# Top candidate is picked without asking when it beats the runner-up by this much.
CLEAR_WINNER_MARGIN = 0.25


def _step() -> int:
    """LangGraph superstep number: stable when a node is retried or resumed, so it makes
    deterministic message / event ids."""
    return get_config()["metadata"]["langgraph_step"]


def _tool(schema: type, name: str) -> StructuredTool:
    # The analyst only *requests* tools; the tools node executes them. The function is unused.
    return StructuredTool.from_function(
        func=lambda **_: None, name=name, description=schema.__doc__, args_schema=schema
    )


ANALYST_TOOLS = [
    _tool(AskReviewer, "ask_reviewer"),
    _tool(SearchNews, "search_news"),
    _tool(SubmitFindings, "submit_findings"),
]


# --- resolve -----------------------------------------------------------------


def _pick(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact = [c for c in candidates if c["name"].lower() == query.strip().lower()]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    if (
        len(candidates) > 1
        and candidates[0]["score"] - candidates[1]["score"] >= CLEAR_WINNER_MARGIN
    ):
        return candidates[0]
    return None


def _label(c: dict[str, Any]) -> str:
    closed = f", closed {c['closed_on']}" if c.get("closed_on") else ""
    return f"{c['name']} — {c['status']}{closed}, {c['address']} (founded {c['founded']})"


async def resolve(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    sink = runtime.context.sink
    query = state["query"]
    key = f"resolve:{_step()}"
    await sink.emit(
        "tool",
        {"id": key, "name": "search_companies", "args": {"name": query}, "status": "started"},
        key=f"{key}:start",
    )
    async with SessionLocal() as s:
        candidates = await data.search_companies(s, query)
    await sink.emit(
        "tool",
        {
            "id": key,
            "name": "search_companies",
            "status": "finished",
            "summary": f"{len(candidates)} match(es)",
            "result": [c["name"] for c in candidates],
        },
        key=f"{key}:end",
    )

    chosen = _pick(query, candidates)
    if chosen is None and candidates:
        question = {
            "question_id": f"q-{key}",
            "kind": "single",
            "question": f"I found {len(candidates)} companies with names similar to "
            f"“{query}”. Which one did you mean?",
            "options": [{"id": c["id"], "label": _label(c)} for c in candidates],
        }
        await sink.emit("question", question, key=question["question_id"])
        answer = interrupt(question)
        chosen = next((c for c in candidates if c["id"] == answer), None)
    elif chosen is None:
        question = {
            "question_id": f"q-{key}",
            "kind": "text",
            "question": f"I couldn't find any company matching “{query}”. "
            "What is the exact registered name?",
            "options": [],
        }
        await sink.emit("question", question, key=question["question_id"])
        return {"query": str(interrupt(question)), "candidates": []}

    if chosen is None:  # answer didn't match an option; ask again on the next pass
        return {"candidates": candidates}
    await sink.emit(
        "company_selected",
        {"company_id": chosen["id"], "name": chosen["name"]},
        key=f"{key}:selected",
    )
    return {"candidates": candidates, "company": {"id": chosen["id"]}}


def after_resolve(state: CheckState) -> str:
    return "gather" if state.get("company") else "resolve"


# --- gather ------------------------------------------------------------------


async def gather(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    sink = runtime.context.sink
    company_id = state["company"]["id"]
    key = f"gather:{_step()}"
    async with SessionLocal() as s:
        results: dict[str, Any] = {}
        for name, fn in (
            ("get_company", data.get_company),
            ("get_reviews", data.get_reviews),
            ("get_news", data.get_news),
        ):
            await sink.emit(
                "tool",
                {
                    "id": f"{key}:{name}",
                    "name": name,
                    "args": {"company_id": company_id},
                    "status": "started",
                },
                key=f"{key}:{name}:start",
            )
            results[name] = await fn(s, company_id)
            summary = (
                f"{len(results[name])} item(s)"
                if isinstance(results[name], list)
                else results[name]["name"]
            )
            await sink.emit(
                "tool",
                {"id": f"{key}:{name}", "name": name, "status": "finished", "summary": summary},
                key=f"{key}:{name}:end",
            )

    update = {
        "company": results["get_company"],
        "reviews": results["get_reviews"],
        "news": results["get_news"],
        "as_of": state.get("as_of") or date.today().isoformat(),
    }
    return {**update, "messages": [HumanMessage(dossier({**state, **update}))]}


# --- analyst ⇄ tools ---------------------------------------------------------


async def analyst(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    turns = state.get("analyst_turns", 0)
    if turns >= MAX_ANALYST_TURNS:
        raise RuntimeError("analyst did not submit findings within the turn limit")

    sink = runtime.context.sink
    message_id = f"analyst:{_step()}"
    llm = chat_model(tools=ANALYST_TOOLS)
    prompt = [SystemMessage(ANALYST_SYSTEM.format(as_of=state["as_of"])), *state["messages"]]

    agg = None
    wrote = False
    async for chunk in llm.astream(prompt):
        agg = chunk if agg is None else agg + chunk
        if chunk.text:
            wrote = True
            await sink.delta(message_id, chunk.text)
    if wrote:
        await sink.end_message(message_id)
    if agg is None:
        raise RuntimeError("LLM returned an empty response")
    return {"messages": [message_chunk_to_message(agg)], "analyst_turns": turns + 1}


def after_analyst(state: CheckState) -> str:
    return "tools"  # tools handles both tool calls and a missing submit_findings


def _known_evidence(state: CheckState) -> set[str]:
    items = [*state.get("reviews", []), *state.get("news", []), *state.get("extra_news", [])]
    return {e["id"] for e in items} - set(state.get("excluded_ids", []))


async def tools(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    sink = runtime.context.sink
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)

    if not last.tool_calls:
        return {
            "messages": [
                HumanMessage(
                    "Please continue: use the tools, and call submit_findings when you are done."
                )
            ]
        }

    replies: list[ToolMessage] = []
    update: dict[str, Any] = {}
    extra_news = list(state.get("extra_news", []))

    def reply(tc: dict, content: Any) -> None:
        text = content if isinstance(content, str) else json.dumps(content)
        replies.append(ToolMessage(content=text, tool_call_id=tc["id"], name=tc["name"]))

    for tc in last.tool_calls:
        name, args, key = tc["name"], tc["args"], f"tool:{tc['id']}"
        try:
            if name == "ask_reviewer":
                q = AskReviewer.model_validate(args)
                if q.kind != "text" and len(q.options) < 2:
                    raise ValueError("single/multi questions need at least two options")
                question = {
                    "question_id": f"q-{tc['id']}",
                    "kind": q.kind,
                    "question": q.question,
                    "options": [{"id": o, "label": o} for o in q.options],
                }
                await sink.emit("question", question, key=question["question_id"])
                reply(tc, {"reviewer_answer": interrupt(question)})

            elif name == "search_news":
                q = SearchNews.model_validate(args)
                await sink.emit(
                    "tool",
                    {"id": key, "name": name, "args": args, "status": "started"},
                    key=f"{key}:start",
                )
                async with SessionLocal() as s:
                    found = await data.search_news(s, q.text)
                known = {n["id"] for n in [*state.get("news", []), *extra_news]}
                extra_news += [n for n in found if n["id"] not in known]
                await sink.emit(
                    "tool",
                    {
                        "id": key,
                        "name": name,
                        "status": "finished",
                        "summary": f"{len(found)} article(s)",
                    },
                    key=f"{key}:end",
                )
                reply(tc, found)

            elif name == "submit_findings":
                f = SubmitFindings.model_validate(args)
                unknown = {i for c in f.concerns for i in c.evidence_ids} - _known_evidence(
                    {**state, "extra_news": extra_news}
                )
                if unknown:
                    raise ValueError(f"unknown or excluded evidence IDs: {sorted(unknown)}")
                update = {
                    "concerns": [
                        {"id": f"c{i + 1}", **c.model_dump()} for i, c in enumerate(f.concerns)
                    ],
                    "open_questions": f.open_questions,
                    "recommendation": {
                        "decision": f.recommendation,
                        "reason": f.recommendation_reason,
                    },
                    "findings_ready": True,
                }
                await sink.emit("findings", update, key=f"{key}:findings")
                reply(tc, "Findings recorded.")

            else:
                raise ValueError(f"unknown tool {name!r}")

        except (ValidationError, ValueError) as e:
            # Malformed tool call: tell the model what was wrong so it can fix it next turn.
            reply(tc, f"Error: {e}")

    return {"messages": replies, "extra_news": extra_news, **update}


def after_tools(state: CheckState) -> str:
    return "report" if state.get("findings_ready") else "analyst"


# --- report ------------------------------------------------------------------


async def report(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Stream the report. If an earlier attempt was cut off (pause, crash), continue the text
    the reviewer already saw instead of starting over."""
    sink = runtime.context.sink
    message_id = f"report:{_step()}"
    prior, finished = await sink.resume_message(message_id)

    if not finished:
        prompt = [
            SystemMessage(REPORT_SYSTEM.format(as_of=state["as_of"])),
            HumanMessage(report_input(state)),
        ]
        if prior:
            prompt.append(HumanMessage(CONTINUE_REPORT.format(prior=prior)))
        prior = await _stream_continuation(sink, message_id, chat_model(), prompt, prior)
        await sink.end_message(message_id, kind="report")

    await sink.emit("report", {"message_id": message_id, "markdown": prior}, key=message_id)
    return {"report": prior}


async def _stream_continuation(sink, message_id, llm, prompt, prior: str) -> str:
    """Stream the model's output after `prior`; returns the full message text.

    With prior text, the first few characters are held back until we can tell whether the
    model repeated itself (see continuation.decide)."""
    text = prior
    held = ""
    decided = not prior
    async for chunk in llm.astream(prompt):
        piece = chunk.text
        if not piece:
            continue
        if not decided:
            held += piece
            if len(held) < continuation.DECIDE_AFTER:
                continue
            piece, text, decided = await _apply_decision(sink, message_id, prior, held)
        text += piece
        if piece:
            await sink.delta(message_id, piece)
    if not decided:  # short continuation that never reached DECIDE_AFTER
        piece, text, _ = await _apply_decision(sink, message_id, prior, held)
        text += piece
        if piece:
            await sink.delta(message_id, piece)
    return text


async def _apply_decision(sink, message_id, prior: str, held: str) -> tuple[str, str, bool]:
    mode, drop = continuation.decide(prior, held)
    if mode == "restart":
        await sink.restart_message(message_id)
        return held, "", True
    return held[drop:], prior, True


# --- graph -------------------------------------------------------------------


def build_graph(checkpointer=None):
    g = StateGraph(CheckState, context_schema=AgentContext)
    g.add_node("resolve", resolve)
    g.add_node("gather", gather)
    g.add_node("analyst", analyst)
    g.add_node("tools", tools)
    g.add_node("report", report)
    g.add_edge(START, "resolve")
    g.add_conditional_edges("resolve", after_resolve, ["resolve", "gather"])
    g.add_edge("gather", "analyst")
    g.add_conditional_edges("analyst", after_analyst, ["tools"])
    g.add_conditional_edges("tools", after_tools, ["analyst", "report"])
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
