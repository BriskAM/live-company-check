"""The analyst's tools: schemas the LLM sees and the handlers the `tools` node runs.

Adding a tool = a schema in state.py, a handler here, and an entry in HANDLERS.
Handlers validate arguments, do the work, emit events and return the text the LLM gets back.
Raising ToolError (or a validation error) returns the message to the LLM so it can fix the call.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from company_check.agent import data, viz
from company_check.agent.events import EventSink
from company_check.agent.state import (
    AskReviewer,
    ChartNewsTimeline,
    ChartRatingTrend,
    SearchNews,
    SubmitFindings,
    TableCompareCompanies,
    TableRatingPeriods,
)
from company_check.db import SessionLocal


class ToolError(ValueError):
    pass


class Turn:
    """Everything one `tools` node run reads and writes."""

    def __init__(self, state: dict[str, Any], sink: EventSink) -> None:
        self.state = state
        self.sink = sink
        self.update: dict[str, Any] = {}
        self.extra_news = list(state.get("extra_news", []))
        self.charts = dict(state.get("charts", {}))

    def view(self) -> dict[str, Any]:
        """State as seen by later tool calls in the same turn."""
        return {**self.state, **self.update, "extra_news": self.extra_news, "charts": self.charts}

    def known_evidence(self) -> set[str]:
        s = self.view()
        items = [*s.get("reviews", []), *s.get("news", []), *s.get("extra_news", [])]
        return {e["id"] for e in items} - set(s.get("excluded_ids", []))


Handler = Callable[[Turn, dict[str, Any], str], Awaitable[Any]]


# --- questions, search, findings ---------------------------------------------


async def ask_reviewer(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    q = AskReviewer.model_validate(args)
    if q.kind != "text" and len(q.options) < 2:
        raise ToolError("single/multi questions need at least two options")
    question = {
        "question_id": f"q-{call_id}",
        "kind": q.kind,
        "question": q.question,
        "options": [{"id": o, "label": o} for o in q.options],
    }
    await turn.sink.emit("question", question, key=question["question_id"])
    return {"reviewer_answer": interrupt(question)}


async def search_news(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    q = SearchNews.model_validate(args)
    key = f"tool:{call_id}"
    await turn.sink.emit(
        "tool",
        {"id": key, "name": "search_news", "args": args, "status": "started"},
        key=f"{key}:start",
    )
    async with SessionLocal() as s:
        found = await data.search_news(s, q.text)
    known = {n["id"] for n in [*turn.state.get("news", []), *turn.extra_news]}
    turn.extra_news += [n for n in found if n["id"] not in known]
    await turn.sink.emit(
        "tool",
        {
            "id": key,
            "name": "search_news",
            "status": "finished",
            "summary": f"{len(found)} article(s)",
        },
        key=f"{key}:end",
    )
    return found


async def submit_findings(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    f = SubmitFindings.model_validate(args)
    unknown = {i for c in f.concerns for i in c.evidence_ids} - turn.known_evidence()
    if unknown:
        raise ToolError(f"unknown or excluded evidence IDs: {sorted(unknown)}")
    findings = {
        "concerns": [{"id": f"c{i + 1}", **c.model_dump()} for i, c in enumerate(f.concerns)],
        "open_questions": f.open_questions,
        "recommendation": {"decision": f.recommendation, "reason": f.recommendation_reason},
    }
    turn.update.update(findings, findings_ready=True)
    await turn.sink.emit("findings", findings, key=f"tool:{call_id}:findings")
    return "Findings recorded."


# --- visualization -----------------------------------------------------------

# tool name -> (schema, default title); build_spec() turns parameters into data
CHARTS: dict[str, tuple[type[BaseModel], str]] = {
    "chart_rating_trend": (ChartRatingTrend, "Average rating by month"),
    "chart_news_timeline": (ChartNewsTimeline, "News timeline"),
    "table_rating_periods": (TableRatingPeriods, "Ratings by period"),
    "table_compare_companies": (TableCompareCompanies, "Similar companies"),
}


def _merge_params(tool: str, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Update semantics: fields given override, fields left out keep their old value.
    A new range replaces the old one entirely (last_months vs start/end)."""
    merged = {**old, **{k: v for k, v in new.items() if v is not None}}
    if new.get("last_months"):
        merged.pop("start", None)
        merged.pop("end", None)
    elif new.get("start") or new.get("end"):
        merged.pop("last_months", None)
    return merged


async def build_spec(state: dict[str, Any], tool: str, params: dict[str, Any]) -> dict[str, Any]:
    if tool == "chart_rating_trend":
        return viz.rating_trend(state, params)
    if tool == "chart_news_timeline":
        return viz.news_timeline(state, params)
    if tool == "table_rating_periods":
        if not params.get("periods"):
            raise ToolError("periods are required")
        try:
            return viz.rating_periods(state, params)
        except viz.PeriodError as e:
            raise ToolError(str(e)) from e
    if tool == "table_compare_companies":
        ids = params.get("company_ids") or []
        if len(ids) < 2:
            raise ToolError("give at least two company_ids")
        rows = []
        async with SessionLocal() as s:
            for cid in ids:
                c = await data.get_company(s, cid)
                if c is None:
                    raise ToolError(f"unknown company id {cid}")
                reviews = await data.get_reviews(s, cid)
                news = await data.get_news(s, cid)
                avg = (
                    round(sum(r["rating"] for r in reviews) / len(reviews), 2) if reviews else None
                )
                rows.append(
                    {
                        **{k: c[k] for k in ("name", "status", "founded", "closed_on", "address")},
                        "reviews": len(reviews),
                        "avg_rating": avg,
                        "news": len(news),
                    }
                )
        return viz.company_comparison(rows)
    raise ToolError(f"unknown chart tool {tool!r}")


async def emit_chart(sink: EventSink, chart_id: str, chart: dict[str, Any], spec: dict) -> None:
    await sink.emit(
        "chart",
        {
            "chart_id": chart_id,
            "version": chart["version"],
            "tool": chart["tool"],
            "title": chart["title"],
            "params": chart["params"],
            "spec": spec,
        },
        key=f"chart:{chart_id}:v{chart['version']}",
    )


def chart_handler(tool: str) -> Handler:
    schema, default_title = CHARTS[tool]

    async def handle(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
        req = schema.model_validate(args).model_dump()
        chart_id = req.pop("chart_id")
        title = req.pop("title")
        if chart_id:
            old = turn.charts.get(chart_id)
            if old is None:
                raise ToolError(f"no chart {chart_id!r}; existing: {sorted(turn.charts)}")
            if old["tool"] != tool:
                raise ToolError(f"{chart_id} is a {old['tool']} chart; use that tool")
            params = _merge_params(tool, old["params"], req)
            chart = {
                **old,
                "params": params,
                "title": title or old["title"],
                "version": old["version"] + 1,
            }
        else:
            chart_id = f"chart-{len(turn.charts) + 1}"
            params = {k: v for k, v in req.items() if v is not None}
            chart = {"tool": tool, "params": params, "title": title or default_title, "version": 1}
        spec = await build_spec(turn.view(), tool, params)
        turn.charts[chart_id] = chart
        await emit_chart(turn.sink, chart_id, chart, spec)
        verb = "updated" if chart["version"] > 1 else "created"
        return f"{chart_id} {verb} (v{chart['version']}) and shown: {viz.summarize(spec)}"

    return handle


# --- registry ----------------------------------------------------------------

HANDLERS: dict[str, Handler] = {
    "ask_reviewer": ask_reviewer,
    "search_news": search_news,
    "submit_findings": submit_findings,
    **{name: chart_handler(name) for name in CHARTS},
}

SCHEMAS: dict[str, type[BaseModel]] = {
    "ask_reviewer": AskReviewer,
    "search_news": SearchNews,
    "submit_findings": SubmitFindings,
    **{name: schema for name, (schema, _) in CHARTS.items()},
}


def _tool(name: str, schema: type[BaseModel]) -> StructuredTool:
    # The LLM only *requests* tools; the tools node executes them. The function is unused.
    return StructuredTool.from_function(
        func=lambda **_: None, name=name, description=schema.__doc__, args_schema=schema
    )


ANALYST_TOOLS = [_tool(name, schema) for name, schema in SCHEMAS.items()]


async def run_call(turn: Turn, call: dict[str, Any]) -> str:
    """Run one tool call; errors become a message the model can act on."""
    handler = HANDLERS.get(call["name"])
    try:
        if handler is None:
            raise ToolError(f"unknown tool {call['name']!r}")
        result = await handler(turn, call["args"], call["id"])
    except (ValidationError, ToolError) as e:
        return f"Error: {e}"
    return result if isinstance(result, str) else json.dumps(result)
