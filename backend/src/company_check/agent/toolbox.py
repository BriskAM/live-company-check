"""The analyst's tools: schemas the LLM sees and the handlers the `tools` node runs.

Adding a tool = a schema in state.py, a handler here, and an entry in HANDLERS.
Handlers validate arguments, do the work, emit events and return the text the LLM gets back.
Raising ToolError (or a validation error) returns the message to the LLM so it can fix the call.
"""

import hashlib
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
    DrawChart,
    ExcludeEvidence,
    ReviseReport,
    SearchNews,
    SubmitFindings,
    SwitchCompany,
    TableCompareCompanies,
    TableRatingPeriods,
    UpdateFindings,
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

    def all_evidence(self) -> set[str]:
        s = self.view()
        items = [*s.get("reviews", []), *s.get("news", []), *s.get("extra_news", [])]
        return {e["id"] for e in items}

    def known_evidence(self) -> set[str]:
        return self.all_evidence() - set(self.view().get("excluded_ids", []))

    def add(self, key: str, *items: Any) -> None:
        """Append to a list field of the state (copy-on-write, so retries stay clean)."""
        self.update[key] = [*self.view().get(key, []), *items]

    def changed(self, what: str, *, report: bool = True) -> None:
        """Record a change for the reviewer; by default the report is rewritten for it."""
        self.add("round_changes", what)
        if report:
            self.update["report_dirty"] = True


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
    answer = interrupt(question)
    turn.add("reviewer_notes", f"Q: {q.question} A: {json.dumps(answer)}")
    return {"reviewer_answer": answer}


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
    new = [n for n in found if n["id"] not in known]
    turn.extra_news += new
    if turn.view().get("mode") == "feedback":
        turn.changed(f"searched news for “{q.text}”: {len(new)} new article(s)", report=False)
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


# --- feedback: findings, evidence, report, company ---------------------------


def _findings(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "concerns": s.get("concerns", []),
        "open_questions": s.get("open_questions", []),
        "recommendation": s.get("recommendation", {}),
    }


async def emit_findings(turn: Turn, key: str, changes: list[str]) -> None:
    await turn.sink.emit("findings", {**_findings(turn.view()), "changes": changes}, key=key)


async def exclude_evidence(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    q = ExcludeEvidence.model_validate(args)
    unknown = set(q.evidence_ids) - turn.all_evidence()
    if unknown:
        raise ToolError(f"unknown evidence IDs: {sorted(unknown)}")
    excluded = list(turn.view().get("excluded_ids", []))
    new = [i for i in dict.fromkeys(q.evidence_ids) if i not in excluded]
    if not new:
        return f"{q.evidence_ids} were already excluded; nothing changed."
    turn.update["excluded_ids"] = [*excluded, *new]
    turn.add("round_excluded", *new)
    await turn.sink.emit(
        "evidence_excluded", {"ids": new, "reason": q.reason}, key=f"tool:{call_id}:excluded"
    )
    # The report reads the reviews and the cited evidence; excluding an uncited news item
    # changes neither, so it alone doesn't trigger a rewrite.
    review_ids = {r["id"] for r in turn.view().get("reviews", [])}
    turn.changed(f"excluded {', '.join(new)} ({q.reason})", report=bool(review_ids & set(new)))

    # Deterministic part of the recompute: only concerns citing the excluded items change.
    gone = set(new)
    kept, dropped, trimmed = [], [], []
    for c in turn.view().get("concerns", []):
        if not gone & set(c["evidence_ids"]):
            kept.append(c)
            continue
        rest = [i for i in c["evidence_ids"] if i not in gone]
        if rest:
            kept.append({**c, "evidence_ids": rest})
            trimmed.append(c["id"])
        else:
            dropped.append(c["id"])
    if dropped or trimmed:
        turn.update["concerns"] = kept
        changes = [f"removed {i} (its only evidence was excluded)" for i in dropped] + [
            f"took excluded evidence off {i}" for i in trimmed
        ]
        for c in changes:
            turn.changed(c)
        await emit_findings(turn, f"tool:{call_id}:findings", changes)

    redrawn = await rebuild_charts(turn)
    return (
        f"Excluded {new}. Concerns removed (no evidence left): {dropped or 'none'}. "
        f"Concerns that lost some evidence: {trimmed or 'none'} — check their wording still "
        f"holds. Charts redrawn: {redrawn or 'none'}. Use update_findings only for concerns "
        "or a recommendation that must now change."
    )


async def update_findings(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    u = UpdateFindings.model_validate(args)
    concerns = {c["id"]: c for c in turn.view().get("concerns", [])}
    missing = (set(u.remove_concern_ids) | {c.id for c in u.upsert_concerns if c.id}) - set(
        concerns
    )
    if missing:
        raise ToolError(f"no concerns {sorted(missing)}; existing: {sorted(concerns)}")
    unknown = {i for c in u.upsert_concerns for i in c.evidence_ids} - turn.known_evidence()
    if unknown:
        raise ToolError(f"unknown or excluded evidence IDs: {sorted(unknown)}")

    changes = []
    for cid in u.remove_concern_ids:
        concerns.pop(cid)
        changes.append(f"removed {cid}")
    next_n = max((int(i[1:]) for i in concerns if i[1:].isdigit()), default=0)
    for patch in u.upsert_concerns:
        body = patch.model_dump(exclude={"id"})
        if patch.id:
            concerns[patch.id] = {"id": patch.id, **body}
            changes.append(f"revised {patch.id}")
        else:
            next_n += 1
            concerns[f"c{next_n}"] = {"id": f"c{next_n}", **body}
            changes.append(f"added c{next_n}: {patch.title}")
    turn.update["concerns"] = list(concerns.values())
    if u.open_questions is not None:
        turn.update["open_questions"] = u.open_questions
        changes.append("updated open questions")
    if u.recommendation or u.recommendation_reason:
        old = turn.view().get("recommendation", {})
        turn.update["recommendation"] = {
            "decision": u.recommendation or old.get("decision"),
            "reason": u.recommendation_reason or old.get("reason"),
        }
        changes.append(f"recommendation: {turn.update['recommendation']['decision']}")
    if not changes:
        return "Nothing to change."
    for c in changes:
        turn.changed(c)
    await emit_findings(turn, f"tool:{call_id}:findings", changes)
    return f"Findings updated: {'; '.join(changes)}."


async def revise_report(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    r = ReviseReport.model_validate(args)
    turn.add("report_instructions", r.instructions)
    turn.add("report_style", r.instructions)
    turn.changed(f"report: {r.instructions}")
    return "Noted; the report is rewritten with this when you finish."


async def switch_company(turn: Turn, args: dict[str, Any], call_id: str) -> Any:
    r = SwitchCompany.model_validate(args)
    if r.company_id == (turn.view().get("company") or {}).get("id"):
        raise ToolError("that is the company already being checked")
    async with SessionLocal() as s:
        company = await data.get_company(s, r.company_id)
    if company is None:
        raise ToolError(f"unknown company id {r.company_id}")
    turn.update["switch_to"] = r.company_id
    turn.changed(f"restarted the check for {company['name']} ({r.reason})", report=False)
    return f"The check restarts for {company['name']} after this turn. Call no other tools."


# --- visualization -----------------------------------------------------------

# fields a new chart must state, so the model cannot leave "per quarter" to a silent default
REQUIRED_ON_CREATE = {"chart": ("kind", "interval")}

# tool name -> (schema, default title); build_spec() turns parameters into data
CHARTS: dict[str, tuple[type[BaseModel], str]] = {
    "chart": (DrawChart, "Chart"),
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
    if tool == "chart":
        series = params.get("series")
        if not series:
            raise ToolError("series are required: say what to plot")
        current = state.get("company") or {}
        series = [{**sq, "company_id": sq.get("company_id") or current.get("id")} for sq in series]
        companies = {}
        async with SessionLocal() as s:
            for cid in dict.fromkeys(sq["company_id"] for sq in series):
                if cid == current.get("id"):
                    # the checked company comes from the state, which honours its extra news
                    companies[cid] = {
                        "name": current.get("name", cid),
                        "reviews": state.get("reviews", []),
                        "news": [*state.get("news", []), *state.get("extra_news", [])],
                    }
                    continue
                c = await data.get_company(s, cid)
                if c is None:
                    raise ToolError(f"unknown company id {cid}")
                companies[cid] = {
                    "name": c["name"],
                    "reviews": await data.get_reviews(s, cid),
                    "news": await data.get_news(s, cid),
                }
        try:
            return viz.chart(state, {**params, "series": series}, companies)
        except viz.ChartError as e:
            raise ToolError(str(e)) from e
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


def _digest(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


async def rebuild_charts(turn: Turn) -> list[str]:
    """Redraw every chart from the current state; emit a new version only where the data
    changed (e.g. an excluded article disappears from the timeline)."""
    redrawn = []
    for chart_id, chart in list(turn.charts.items()):
        spec = await build_spec(turn.view(), chart["tool"], chart["params"])
        digest = _digest(spec)
        if digest == chart.get("digest"):
            continue
        chart = {**chart, "version": chart["version"] + 1, "digest": digest}
        turn.charts[chart_id] = chart
        await emit_chart(turn.sink, chart_id, chart, spec)
        redrawn.append(chart_id)
    return redrawn


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
            seq = turn.view().get("chart_seq", len(turn.charts)) + 1
            turn.update["chart_seq"] = seq
            chart_id = f"chart-{seq}"
            params = {k: v for k, v in req.items() if v is not None}
            missing = [f for f in REQUIRED_ON_CREATE.get(tool, ()) if f not in params]
            if missing:
                raise ToolError(f"a new chart needs {', '.join(missing)}; call again with them")
            chart = {"tool": tool, "params": params, "title": title or default_title, "version": 1}
        spec = await build_spec(turn.view(), tool, params)
        chart["digest"] = _digest(spec)
        if chart["version"] > 1:
            old = turn.charts[chart_id]
            if chart["digest"] == old.get("digest") and chart["title"] == old["title"]:
                return f"{chart_id} already shows exactly that; nothing changed."
        turn.charts[chart_id] = chart
        await emit_chart(turn.sink, chart_id, chart, spec)
        verb = "updated" if chart["version"] > 1 else "created"
        if turn.view().get("mode") == "feedback":
            turn.changed(f"{verb} {chart_id} ({chart['title']})", report=False)
        return f"{chart_id} {verb} (v{chart['version']}) and shown: {viz.summarize(spec)}"

    return handle


# --- registry ----------------------------------------------------------------

HANDLERS: dict[str, Handler] = {
    "ask_reviewer": ask_reviewer,
    "search_news": search_news,
    "submit_findings": submit_findings,
    "exclude_evidence": exclude_evidence,
    "update_findings": update_findings,
    "revise_report": revise_report,
    "switch_company": switch_company,
    **{name: chart_handler(name) for name in CHARTS},
}

SCHEMAS: dict[str, type[BaseModel]] = {
    "ask_reviewer": AskReviewer,
    "search_news": SearchNews,
    "submit_findings": SubmitFindings,
    "exclude_evidence": ExcludeEvidence,
    "update_findings": UpdateFindings,
    "revise_report": ReviseReport,
    "switch_company": SwitchCompany,
    **{name: schema for name, (schema, _) in CHARTS.items()},
}

# Tools offered to the model: the first pass investigates and submits findings; a feedback
# round patches what exists.
CHECK_TOOL_NAMES = ["ask_reviewer", "search_news", "submit_findings", *CHARTS]
FEEDBACK_TOOL_NAMES = [
    "exclude_evidence",
    "update_findings",
    "revise_report",
    "switch_company",
    "search_news",
    "ask_reviewer",
    *CHARTS,
]


def _tool(name: str, schema: type[BaseModel]) -> StructuredTool:
    # The LLM only *requests* tools; the tools node executes them. The function is unused.
    return StructuredTool.from_function(
        func=lambda **_: None, name=name, description=schema.__doc__, args_schema=schema
    )


CHECK_TOOLS = [_tool(name, SCHEMAS[name]) for name in CHECK_TOOL_NAMES]
FEEDBACK_TOOLS = [_tool(name, SCHEMAS[name]) for name in FEEDBACK_TOOL_NAMES]
FINISH_TOOLS = [_tool("submit_findings", SubmitFindings)]  # offered alone at the turn limit


async def run_call(turn: Turn, call: dict[str, Any]) -> str:
    """Run one tool call; errors become a message the model can act on."""
    handler = HANDLERS.get(call["name"])
    try:
        if handler is None:
            raise ToolError(f"unknown tool {call['name']!r}")
        result = await handler(turn, call["args"], call["id"])
    except (ValidationError, ToolError) as e:
        return f"Error: {e}"
    if turn.view().get("mode") == "feedback":
        turn.add("round_ops", call["name"])
    return result if isinstance(result, str) else json.dumps(result)
