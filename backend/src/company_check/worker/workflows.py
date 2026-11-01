"""Temporal workflow: one per check. Owns control flow — running, waiting, paused, error.

The workflow never touches the DB or the LLM (that would break deterministic replay).
It only decides *when* to run the agent activity and holds the reviewer's commands:

    ┌──────────────── loop ────────────────┐
    │ wait until not paused                │
    │ run_agent(answers so far)            │──▶ "waiting": wait for all answers
    │   cancelled by pause → paused        │──▶ "done": completed
    │   failed after retries → error,      │
    │   wait for retry signal              │
    └──────────────────────────────────────┘

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
    from company_check.worker.types import (
        AnswerSignal,
        CheckInput,
        ControlSignal,
        EmitInput,
        RunAgentInput,
    )

AGENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
)
EMIT_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=10))


@workflow.defn
class CheckWorkflow:
    def __init__(self) -> None:
        self.check_id = ""
        self.status = "starting"
        self.paused = False
        self.questions: list[dict[str, Any]] = []  # currently pending
        self.answers: dict[str, Any] = {}  # question_id -> value (first answer wins)
        self.retry_requested = False
        self._seen_commands: set[str] = set()
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
        self._seen_commands.add(command_id)
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
        }

    # --- main loop -------------------------------------------------------------

    @workflow.run
    async def run(self, inp: CheckInput) -> None:
        self.check_id = inp.check_id
        while True:
            await workflow.wait_condition(lambda: not self.paused)
            await self._set_status("running")
            if self.paused:  # pause arrived while the status was being written
                continue
            self._agent_task = workflow.start_activity(
                run_agent,
                RunAgentInput(inp.check_id, inp.query, dict(self.answers)),
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
                await self._set_status("error", message=str(e.cause or e))
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
            await self._set_status("completed")
            # M6: wait for feedback here and run another round.
            return
