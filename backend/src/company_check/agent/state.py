"""Agent state, checkpointed by LangGraph after every node.

Kept structured (not just a transcript) so feedback can change one piece — exclude an
evidence item, re-draw a chart — and only the work depending on it is redone.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from company_check.agent.events import EventSink


class CheckState(TypedDict, total=False):
    query: str
    as_of: str  # ISO date the check runs "as of" (relative ranges like "last 12 months")
    candidates: list[dict[str, Any]]
    company: dict[str, Any] | None
    reviews: list[dict[str, Any]]
    news: list[dict[str, Any]]
    extra_news: list[dict[str, Any]]  # found via search_news (e.g. under previous names)
    excluded_ids: list[str]  # evidence the reviewer (or agent) ruled out
    messages: Annotated[list[AnyMessage], add_messages]  # analyst conversation
    analyst_turns: int
    concerns: list[dict[str, Any]]
    open_questions: list[str]
    recommendation: dict[str, Any]
    findings_ready: bool  # submit_findings accepted → write the report
    report: str
    # chart_id -> {"tool", "title", "params", "version"}; data is rebuilt from state on demand
    charts: dict[str, dict[str, Any]]


@dataclass
class AgentContext:
    """Per-run dependencies injected by the runner (not checkpointed)."""

    sink: EventSink


# --- LLM tool schemas ---------------------------------------------------------


class AskReviewer(BaseModel):
    """Ask the human reviewer a question and wait for the answer."""

    kind: Literal["single", "multi", "text"] = Field(
        description="single = pick one option, multi = pick any options, text = free text"
    )
    question: str = Field(description="The question, one or two sentences")
    options: list[str] = Field(
        default_factory=list, description="Choices for single/multi; empty for text"
    )


class SearchNews(BaseModel):
    """Search all news headlines and text for a phrase (e.g. a previous company name)."""

    text: str


class Concern(BaseModel):
    title: str = Field(description="Short title of the concern")
    detail: str = Field(description="One or two sentences explaining it")
    severity: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(description="IDs of the reviews/news that support it")


class SubmitFindings(BaseModel):
    """Record the final findings. Call exactly once when the investigation is complete."""

    concerns: list[Concern]
    open_questions: list[str] = Field(
        default_factory=list, description="Things the evidence cannot answer"
    )
    recommendation: Literal["proceed", "proceed_with_caution", "do_not_proceed", "needs_more_info"]
    recommendation_reason: str


# --- visualization tools --------------------------------------------------------
# Every chart tool takes an optional chart_id: omit it to create a new chart, pass an existing
# one to update that chart in place. On update, fields left out keep their previous values.


class Marker(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    label: str = Field(description="Short label, e.g. 'Sold to Cobalt'")


class ChartRatingTrend(BaseModel):
    """Line chart of the average customer rating per month (computed from the reviews)."""

    chart_id: str | None = Field(default=None, description="Existing chart to update")
    title: str | None = None
    last_months: int | None = Field(
        default=None, description="Only the last N complete months (overrides start/end)"
    )
    start: str | None = Field(default=None, description="First month, YYYY-MM")
    end: str | None = Field(default=None, description="Last month, YYYY-MM")
    markers: list[Marker] | None = Field(
        default=None, description="Dated events to mark on the chart; [] removes all markers"
    )


class ChartNewsTimeline(BaseModel):
    """Timeline of the company's news items (excluded evidence is left out automatically)."""

    chart_id: str | None = Field(default=None, description="Existing chart to update")
    title: str | None = None
    last_months: int | None = None
    start: str | None = Field(default=None, description="First month, YYYY-MM")
    end: str | None = Field(default=None, description="Last month, YYYY-MM")


class Period(BaseModel):
    label: str
    start: str | None = Field(default=None, description="YYYY-MM-DD, inclusive; omit = open")
    end: str | None = Field(default=None, description="YYYY-MM-DD, inclusive; omit = open")


class TableRatingPeriods(BaseModel):
    """Table comparing review count and average rating across labelled periods (e.g. before
    and after an event)."""

    chart_id: str | None = Field(default=None, description="Existing table to update")
    title: str | None = None
    periods: list[Period] | None = None


class TableCompareCompanies(BaseModel):
    """Table comparing similarly named companies side by side (status, dates, address,
    review and news counts)."""

    chart_id: str | None = Field(default=None, description="Existing table to update")
    title: str | None = None
    company_ids: list[str] | None = None
