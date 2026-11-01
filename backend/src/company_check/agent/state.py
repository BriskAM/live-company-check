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
