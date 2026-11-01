"""Payloads exchanged between the API, the workflow and activities (plain dataclasses, JSON)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckInput:
    check_id: str
    query: str
    # Set when the workflow continues as new: what the previous run carried over.
    carry: dict[str, Any] | None = None


@dataclass
class RunAgentInput:
    check_id: str
    query: str
    # All answers accepted so far, by question_id. The activity applies the ones matching
    # the graph's pending interrupts, so re-sending them is harmless.
    answers: dict[str, Any] = field(default_factory=dict)
    # The feedback this run should handle ({"id", "text", "target"}), or None for the
    # initial check. The activity starts a feedback round only once per feedback id.
    feedback: dict[str, Any] | None = None


@dataclass
class RunAgentResult:
    status: str  # "waiting" (questions pending) | "done"
    questions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EmitInput:
    check_id: str
    type: str
    payload: dict[str, Any]
    key: str | None = None


# --- signals -----------------------------------------------------------------
# Every reviewer command carries a client-generated command_id; the workflow ignores repeats.


@dataclass
class AnswerSignal:
    command_id: str
    question_id: str
    value: Any


@dataclass
class ControlSignal:
    command_id: str


@dataclass
class FeedbackSignal:
    command_id: str
    text: str
    # What the feedback is about, e.g. {"kind": "chart", "id": "chart-1"}; None = general.
    target: dict[str, Any] | None = None
