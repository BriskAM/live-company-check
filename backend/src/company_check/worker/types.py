"""Payloads exchanged between the API, the workflow and activities (plain dataclasses, JSON)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckInput:
    check_id: str
    query: str


@dataclass
class RunAgentInput:
    check_id: str
    query: str
    # All answers accepted so far, by question_id. The activity applies the ones matching
    # the graph's pending interrupts, so re-sending them is harmless.
    answers: dict[str, Any] = field(default_factory=dict)


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
