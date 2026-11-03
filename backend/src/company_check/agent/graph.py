"""The company-check agent as a LangGraph state machine.

    START ─┬─▶ resolve ──▶ gather ──▶ analyst ⇄ tools ──▶ report ──▶ finish_round ──▶ END
           │     ▲  │         ▲                  │  │                       ▲
           │     └──┘         └── switch_company ┘  └── nothing to rewrite ──┘
           └─▶ feedback_intake ──▶ analyst … (feedback round)
                     └──▶ END (same feedback as last time: nothing to do)

- The first run checks the company. Each later run handles one piece of reviewer feedback:
  the analyst patches findings and charts with feedback tools, and the report is rewritten
  only if something it depends on changed.

- resolve / gather are deterministic code: they call the data tools directly (cheap, reliable).
- analyst is one LLM call per node run; tools executes its tool calls in a separate node.
  Splitting them matters for resume: `interrupt()` re-runs the node it is in, so a question
  asked inside `tools` never repeats the (already checkpointed) LLM call.
- Every node streams progress through the injected EventSink.
"""

import re
from datetime import date
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from company_check.agent import continuation, data
from company_check.agent.llm import chat_model
from company_check.agent.prompts import (
    ANALYST_SYSTEM,
    CONTINUE_REPORT,
    FEEDBACK_SYSTEM,
    REPORT_SYSTEM,
    REVISE_REPORT,
    dossier,
    report_input,
    round_context,
)
from company_check.agent.state import AgentContext, CheckState
from company_check.agent.toolbox import (
    CHARTS,
    CHECK_TOOLS,
    FEEDBACK_TOOLS,
    FINISH_TOOLS,
    Turn,
    run_call,
)
from company_check.db import SessionLocal

# Guard against an analyst that never finishes: after MAX_ANALYST_TURNS it must finish
# (only submit_findings is offered, or no tools in a feedback round); if it still doesn't
# within FINAL_TURNS, the run fails.
MAX_ANALYST_TURNS = 8
FINAL_TURNS = 2


class AgentGaveUp(RuntimeError):
    """The analyst could not finish. Retrying the same step would fail the same way."""


# Top candidate is picked without asking when it beats the runner-up by this much.
CLEAR_WINNER_MARGIN = 0.25


def _step() -> int:
    """LangGraph superstep number: stable when a node is retried or resumed, so it makes
    deterministic message / event ids."""
    return get_config()["metadata"]["langgraph_step"]


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


def _company_card(c: dict[str, Any]) -> dict[str, Any]:
    """What the UI shows about the company under check."""
    card = {"company_id": c["id"], "name": c["name"]}
    for k in ("status", "founded", "closed_on", "address", "previous_names"):
        if c.get(k):
            card[k] = c[k]
    return card


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
    await sink.emit("company_selected", _company_card(chosen), key=f"{key}:selected")
    return {"candidates": candidates, "company": {"id": chosen["id"]}}


def after_resolve(state: CheckState) -> str:
    return "gather" if state.get("company") else "resolve"


# --- gather ------------------------------------------------------------------


# Everything derived from one company; cleared when the check restarts for another.
FRESH_CHECK = {
    "extra_news": [],
    "excluded_ids": [],
    "concerns": [],
    "open_questions": [],
    "recommendation": {},
    "findings_ready": False,
    "report": "",
    "charts": {},
    "reviewer_notes": [],
    "report_style": [],
    "analyst_turns": 0,
    "mode": "check",
    "switch_to": None,
}


async def gather(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    sink = runtime.context.sink
    key = f"gather:{_step()}"
    reset: dict[str, Any] = {}
    if state.get("switch_to"):  # the reviewer meant another company: start over for it
        company_id = state["switch_to"]
        reset = dict(FRESH_CHECK)
    else:
        company_id = state["company"]["id"]
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

    if reset:
        await sink.emit(
            "company_selected", _company_card(results["get_company"]), key=f"{key}:selected"
        )
    update = {
        **reset,
        "company": results["get_company"],
        "reviews": results["get_reviews"],
        "news": results["get_news"],
        "as_of": state.get("as_of") or date.today().isoformat(),
    }
    return {
        **update,
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(dossier({**state, **update})),
        ],
    }


# --- analyst ⇄ tools ---------------------------------------------------------


async def analyst(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    turns = state.get("analyst_turns", 0)
    if turns >= MAX_ANALYST_TURNS + FINAL_TURNS:
        raise AgentGaveUp(f"the agent could not finish within {turns} turns")

    sink = runtime.context.sink
    message_id = f"analyst:{_step()}"
    feedback_round = state.get("mode") == "feedback"
    system = FEEDBACK_SYSTEM if feedback_round else ANALYST_SYSTEM
    prompt = [SystemMessage(system.format(as_of=state["as_of"])), *state["messages"]]
    if turns < MAX_ANALYST_TURNS:
        llm = chat_model(tools=FEEDBACK_TOOLS if feedback_round else CHECK_TOOLS)
    elif feedback_round:
        llm = chat_model()
        prompt.append(HumanMessage("Stop using tools. Reply to the reviewer now."))
    else:
        llm = chat_model(tools=FINISH_TOOLS, tool_choice="any")
        prompt.append(
            HumanMessage("Time is up: call submit_findings now with what you have found.")
        )

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
    await _emit_usage(sink, message_id, agg)
    return {"messages": [message_chunk_to_message(agg)], "analyst_turns": turns + 1}


async def _emit_usage(sink, message_id: str, msg) -> None:
    """Token counts for one LLM call, for the per-check usage shown to the reviewer.
    Keyed by message id, so a retried step counts once (its first attempt's tokens are not
    shown — a small undercount, documented)."""
    usage = getattr(msg, "usage_metadata", None)
    if not usage:
        return
    await sink.emit(
        "usage",
        {
            "message_id": message_id,
            "model": msg.response_metadata.get("model_name"),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
        key=f"usage:{message_id}",
    )


def after_analyst(state: CheckState) -> str:
    return "tools"  # tools handles both tool calls and a missing submit_findings


async def tools(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Execute the analyst's tool calls (see toolbox.py). A question pauses the graph here
    via interrupt(); on resume this node re-runs, and handlers are idempotent so earlier
    calls in the same turn are safe to repeat."""
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    if not last.tool_calls:
        if state.get("mode") == "feedback":
            return {}  # a plain reply ends the feedback round
        return {
            "messages": [
                HumanMessage(
                    "Please continue: use the tools, and call submit_findings when you are done."
                )
            ]
        }

    turn = Turn(state, runtime.context.sink)
    replies = [
        ToolMessage(content=await run_call(turn, tc), tool_call_id=tc["id"], name=tc["name"])
        for tc in last.tool_calls
    ]
    return {
        "messages": replies,
        "extra_news": turn.extra_news,
        "charts": turn.charts,
        **turn.update,
    }


def after_tools(state: CheckState) -> str:
    if state.get("switch_to"):
        return "gather"
    if state.get("mode") == "feedback":
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and not last.tool_calls:
            return "report" if state.get("report_dirty") else "finish_round"
        return "analyst"
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
        if state.get("mode") == "feedback" and state.get("report"):
            # Revise the report the reviewer has read rather than writing a new one.
            prompt.append(
                HumanMessage(
                    REVISE_REPORT.format(
                        previous=state["report"],
                        changes="; ".join(state.get("round_changes", [])) or "nothing",
                        instructions=_style_instructions(state),
                    )
                )
            )
        if prior:
            prompt.append(HumanMessage(CONTINUE_REPORT.format(prior=prior)))
        prior, response = await _stream_continuation(sink, message_id, chat_model(), prompt, prior)
        await sink.end_message(message_id, kind="report")
        if response is not None:
            await _emit_usage(sink, message_id, response)

    await sink.emit(
        "report",
        {"message_id": message_id, "markdown": prior, "previous": state.get("report") or None},
        key=message_id,
    )
    return {"report": prior}


def _style_instructions(state: CheckState) -> str:
    """The reviewer's wording requests: this round's, plus earlier ones that must survive
    the rewrite (a later rewrite must not undo "put the recommendation in bold")."""
    new = state.get("report_instructions", [])
    earlier = [i for i in state.get("report_style", []) if i not in new]
    parts = []
    if new:
        parts.append("The reviewer now asks: " + " ".join(new))
    if earlier:
        parts.append(
            "Earlier requests from the reviewer that still apply (if two conflict, the later "
            "one wins):\n" + "\n".join(f"- {i}" for i in earlier)
        )
    return "\n".join(parts)


async def _stream_continuation(sink, message_id, llm, prompt, prior: str):
    """Stream the model's output after `prior`; returns (full message text, the aggregated
    response for its token usage).

    With prior text, the first few characters are held back until we can tell whether the
    model repeated itself (see continuation.decide)."""
    text = prior
    held = ""
    decided = not prior
    response = None
    async for chunk in llm.astream(prompt):
        response = chunk if response is None else response + chunk
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
    return text, response


async def _apply_decision(sink, message_id, prior: str, held: str) -> tuple[str, str, bool]:
    mode, drop = continuation.decide(prior, held)
    if mode == "restart":
        await sink.restart_message(message_id)
        return held, "", True
    return held[drop:], prior, True


# --- feedback rounds -----------------------------------------------------------


def route_start(state: CheckState) -> str:
    return "feedback_intake" if state.get("feedback") else "resolve"


def _norm(feedback: dict[str, Any]) -> str:
    text = re.sub(r"[^a-z0-9_ ]+", " ", feedback["text"].lower())
    return f"{feedback.get('target') or ''}|{' '.join(text.split())}"


# What an exclusion round may also do and still be a no-op to repeat: tidy the findings, or
# draw a chart (the model sometimes adds a news timeline unasked; it already reflects the
# exclusion).
EXCLUSION_OPS = {"exclude_evidence", "update_findings", *CHARTS}


def _already_applied(state: CheckState, norm: str) -> bool:
    """True when repeating this feedback cannot change anything:
    - it is the same as the last applied feedback (nothing has changed since), or
    - an earlier identical feedback excluded evidence, and all of it is still excluded
      (exclusions are never undone, so later rounds cannot have reversed it). Concern,
      recommendation and chart changes made in that round are allowed: they follow from
      the exclusion rather than being separate requests."""
    applied = [f for f in state.get("feedback_log", []) if f.get("outcome") == "applied"]
    if applied and applied[-1]["norm"] == norm:
        return True
    excluded = set(state.get("excluded_ids", []))
    return any(
        f["norm"] == norm
        and "exclude_evidence" in f.get("ops", [])
        and set(f["ops"]) <= EXCLUSION_OPS
        and f.get("excluded")
        and set(f["excluded"]) <= excluded
        for f in applied
    )


async def feedback_intake(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Start a feedback round with a fresh, compact context (dossier + current findings +
    feedback log) instead of the whole transcript, so long sessions stay cheap.

    Feedback identical to the last applied feedback is a no-op: everything it asked for is
    already in place, so no LLM call is made."""
    fb = state["feedback"]
    norm = _norm(fb)
    if _already_applied(state, norm):
        result = {
            "feedback_id": fb["id"],
            "outcome": "duplicate",
            "changes": [],
            "message": "Same as your previous feedback, which is already applied. Nothing changed.",
        }
        await runtime.context.sink.emit(
            "feedback_result", result, key=f"feedback:{fb['id']}:result"
        )
        entry = {"id": fb["id"], "text": fb["text"], "norm": norm, "outcome": "duplicate"}
        return {"feedback_log": [*state.get("feedback_log", []), entry]}
    return {
        "mode": "feedback",
        "analyst_turns": 0,
        "round_changes": [],
        "round_ops": [],
        "round_excluded": [],
        "report_dirty": False,
        "report_instructions": [],
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(dossier(state)),
            HumanMessage(round_context(state, fb)),
        ],
    }


def after_intake(state: CheckState) -> str:
    log = state.get("feedback_log", [])
    return END if log and log[-1]["id"] == state["feedback"]["id"] else "analyst"


async def finish_round(state: CheckState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    fb = state.get("feedback")
    log = state.get("feedback_log", [])
    if not fb or any(f["id"] == fb["id"] for f in log):
        return {}  # the first check, not a feedback round
    changes = state.get("round_changes", [])
    await runtime.context.sink.emit(
        "feedback_result",
        {"feedback_id": fb["id"], "outcome": "applied", "changes": changes},
        key=f"feedback:{fb['id']}:result",
    )
    entry = {
        "id": fb["id"],
        "text": fb["text"],
        "norm": _norm(fb),
        "outcome": "applied",
        "changes": changes,
        "ops": sorted(set(state.get("round_ops", []))),
        "excluded": state.get("round_excluded", []),
    }
    return {
        "feedback_log": [*log, entry],
        "round_changes": [],
        "report_dirty": False,
        "report_instructions": [],
    }


# --- graph -------------------------------------------------------------------


def build_graph(checkpointer=None):
    g = StateGraph(CheckState, context_schema=AgentContext)
    g.add_node("resolve", resolve)
    g.add_node("gather", gather)
    g.add_node("analyst", analyst)
    g.add_node("tools", tools)
    g.add_node("report", report)
    g.add_node("feedback_intake", feedback_intake)
    g.add_node("finish_round", finish_round)
    g.add_conditional_edges(START, route_start, ["resolve", "feedback_intake"])
    g.add_conditional_edges("feedback_intake", after_intake, ["analyst", END])
    g.add_conditional_edges("resolve", after_resolve, ["resolve", "gather"])
    g.add_edge("gather", "analyst")
    g.add_conditional_edges("analyst", after_analyst, ["tools"])
    g.add_conditional_edges("tools", after_tools, ["analyst", "report", "gather", "finish_round"])
    g.add_edge("report", "finish_round")
    g.add_edge("finish_round", END)
    return g.compile(checkpointer=checkpointer)
