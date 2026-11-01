"""Temporal workflow: one per check. Owns control flow — running, waiting, paused, error.

The workflow never touches the DB or the LLM (that would break deterministic replay).
It only decides *when* to run the agent activity and holds the reviewer's commands:

    run the check ──▶ completed ──▶ wait for feedback ──▶ run a feedback round ──┐
                          ▲                                                     │
                          └─────────────────────────────────────────────────────┘

    one run = loop:
      wait until not paused
      run_agent(answers so far, feedback)  ──▶ "waiting": wait for all answers
        cancelled by pause → paused        ──▶ "done": back to completed
        failed after retries → error, wait for retry signal

Feedback that arrives while the agent is busy is queued and handled in order afterwards.
After a number of rounds the workflow continues as new, carrying only what it needs, so a
long review session keeps a small history.

State lives in Temporal's event history, so a paused or waiting check survives restarts of
the API, the worker and (with persistence) the Temporal server.
"""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from company_check.worker.activities import emit_event, run_agent
    from company_check.worker.errors import describe
    from company_check.worker.types import (
        AnswerSignal,
        CheckInput,
        ControlSignal,
        EmitInput,
        FeedbackSignal,
        RunAgentInput,
    )

AGENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
)
EMIT_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=10))

# Feedback rounds per workflow run before continuing as new (keeps the history small).
ROUNDS_PER_RUN = 8
# Command ids remembered across continue-as-new, for deduping late repeats.
SEEN_COMMANDS_KEPT = 500
# A completed check with no feedback for this long closes; feedback after that gets a 409.
IDLE_CLOSE_AFTER = timedelta(days=30)


def _failure_text(e: BaseException) -> str:
    """The innermost failure's type and message (ActivityError wraps the real cause)."""
    while e.__cause__ is not None or getattr(e, "cause", None) is not None:
        e = getattr(e, "cause", None) or e.__cause__
    kind = getattr(e, "type", None) or type(e).__name__
    return f"{kind}: {e}"


@workflow.defn
class CheckWorkflow:
    def __init__(self) -> None:
        self.check_id = ""
        self.status = "starting"
        self.paused = False
        self.questions: list[dict[str, Any]] = []  # currently pending
        self.answers: dict[str, Any] = {}  # question_id -> value (first answer wins)
        self.retry_requested = False
        self.feedback_queue: list[dict[str, Any]] = []
        self.rounds = 0  # feedback rounds handled in this run
        self._seen_commands: dict[str, None] = {}  # insertion-ordered set
        self._agent_task: asyncio.Task | None = None
        # Set when a pause cancels the running agent, so the loop treats that cancellation as
        # a pause even if a resume arrives before the activity has finished stopping.
        self._cancelled_by_pause = False

    # --- helpers ---------------------------------------------------------------

    async def _emit(self, type: str, payload: dict[str, Any], key: str | None = None) -> None:
        await workflow.execute_activity(
            emit_event,
            EmitInput(self.check_id, type, payload, key),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=EMIT_RETRY,
        )

    async def _set_status(self, status: str, **extra: Any) -> None:
        self.status = status
        await self._emit("status", {"status": status, **extra})

    def _first_time(self, command_id: str) -> bool:
        if command_id in self._seen_commands:
            return False
        self._seen_commands[command_id] = None
        return True

    def _all_answered(self) -> bool:
        return all(q["question_id"] in self.answers for q in self.questions)

    # --- signals & queries -----------------------------------------------------

    @workflow.signal
    async def answer(self, sig: AnswerSignal) -> None:
        if not self._first_time(sig.command_id):
            return
        pending_ids = {q["question_id"] for q in self.questions}
        if sig.question_id not in pending_ids or sig.question_id in self.answers:
            return  # double click, stale tab, or unknown question
        self.answers[sig.question_id] = sig.value
        await self._emit(
            "answer",
            {"question_id": sig.question_id, "value": sig.value},
            key=f"answer:{sig.question_id}",
        )

    @workflow.signal
    async def feedback(self, sig: FeedbackSignal) -> None:
        if not self._first_time(sig.command_id):
            return
        item = {"id": sig.command_id, "text": sig.text, "target": sig.target}
        self.feedback_queue.append(item)
        await self._emit(
            "feedback",
            {**item, "queued": self.status != "completed" or len(self.feedback_queue) > 1},
            key=f"feedback:{sig.command_id}",
        )

    @workflow.signal
    async def pause(self, sig: ControlSignal) -> None:
        if not self._first_time(sig.command_id) or self.paused:
            return
        if self.status not in ("running", "waiting_for_input"):
            return
        self.paused = True
        if self._agent_task is not None:
            self._cancelled_by_pause = True
            self._agent_task.cancel()  # the activity flushes its partial output and stops
        await self._set_status("paused")

    @workflow.signal
    async def resume(self, sig: ControlSignal) -> None:
        if not self._first_time(sig.command_id) or not self.paused:
            return
        self.paused = False
        # The run loop sets "running" when it restarts the agent; if we are only waiting
        # for answers, say so now.
        if self.questions and not self._all_answered():
            await self._set_status("waiting_for_input", questions=self.questions)

    @workflow.signal
    async def retry(self, sig: ControlSignal) -> None:
        if self._first_time(sig.command_id) and self.status == "error":
            self.retry_requested = True

    @workflow.query
    def state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "paused": self.paused,
            "questions": self.questions,
            "answers": self.answers,
            "feedback_queue": self.feedback_queue,
        }

    # --- main loop -------------------------------------------------------------

    @workflow.run
    async def run(self, inp: CheckInput) -> None:
        self.check_id = inp.check_id
        if inp.carry:
            self.feedback_queue = inp.carry["feedback_queue"]
            self._seen_commands = dict.fromkeys(inp.carry["seen_commands"])
            self.status = "completed"  # already announced by the previous run
        else:
            await self._run_agent(inp.query, None)

        while True:
            if self.rounds >= ROUNDS_PER_RUN or workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                workflow.continue_as_new(
                    CheckInput(
                        inp.check_id,
                        inp.query,
                        carry={
                            "feedback_queue": self.feedback_queue,
                            "seen_commands": list(self._seen_commands)[-SEEN_COMMANDS_KEPT:],
                        },
                    )
                )
            try:
                await workflow.wait_condition(
                    lambda: bool(self.feedback_queue), timeout=IDLE_CLOSE_AFTER
                )
            except TimeoutError:
                return
            await self._run_agent(inp.query, self.feedback_queue[0])
            self.feedback_queue.pop(0)
            self.rounds += 1

    async def _run_agent(self, query: str, feedback: dict[str, Any] | None) -> None:
        """Run the agent until it has finished the check (or the feedback round)."""
        self.answers = {}
        while True:
            await workflow.wait_condition(lambda: not self.paused)
            await self._set_status("running")
            if self.paused:  # pause arrived while the status was being written
                continue
            self._agent_task = workflow.start_activity(
                run_agent,
                RunAgentInput(self.check_id, query, dict(self.answers), feedback),
                start_to_close_timeout=timedelta(minutes=10),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=AGENT_RETRY,
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            try:
                result = await self._agent_task
            except (ActivityError, asyncio.CancelledError) as e:
                if self._cancelled_by_pause:
                    self._cancelled_by_pause = False
                    continue  # pause cancelled it; loop waits for resume
                if isinstance(e, asyncio.CancelledError):
                    raise  # the workflow itself was cancelled
                await self._set_status("error", **describe(_failure_text(e)))
                await workflow.wait_condition(lambda: self.retry_requested)
                self.retry_requested = False
                continue
            finally:
                self._agent_task = None
            if self._cancelled_by_pause:
                # The pause arrived just as the agent finished: keep the result.
                self._cancelled_by_pause = False

            if result.status == "waiting":
                self.questions = result.questions
                if not self._all_answered():
                    if not self.paused:
                        await self._set_status("waiting_for_input", questions=self.questions)
                    await workflow.wait_condition(self._all_answered)
                continue

            self.questions = []
            # Finished just as a pause arrived: nothing is left to pause.
            self.paused = False
            await self._set_status("completed")
            return
