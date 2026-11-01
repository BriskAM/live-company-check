"""CheckWorkflow control flow with fake activities (no DB, no LLM).

Uses Temporal's time-skipping test server, so waits and retries run instantly.
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from company_check.worker import workflows
from company_check.worker.types import (
    AnswerSignal,
    CheckInput,
    ControlSignal,
    EmitInput,
    FeedbackSignal,
    RunAgentInput,
    RunAgentResult,
)
from company_check.worker.workflows import CheckWorkflow

QUESTION = {"question_id": "q1", "kind": "single", "question": "Which?", "options": []}


class Fakes:
    """Records calls; `script` decides what run_agent does on each call."""

    def __init__(self, script):
        self.script = script
        self.runs: list[RunAgentInput] = []
        self.events: list[EmitInput] = []

    def activities(self):
        @activity.defn(name="run_agent")
        async def run_agent(inp: RunAgentInput) -> RunAgentResult:
            self.runs.append(inp)
            return await self.script(len(self.runs), inp)

        @activity.defn(name="emit_event")
        async def emit_event(inp: EmitInput) -> None:
            self.events.append(inp)

        return [run_agent, emit_event]

    def statuses(self) -> list[str]:
        return [e.payload["status"] for e in self.events if e.type == "status"]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def _start(env, fakes) -> tuple[Worker, WorkflowHandle]:
    worker = Worker(
        env.client,
        task_queue="test",
        workflows=[CheckWorkflow],
        activities=fakes.activities(),
        max_heartbeat_throttle_interval=timedelta(milliseconds=100),
        default_heartbeat_throttle_interval=timedelta(milliseconds=100),
    )
    handle = await env.client.start_workflow(
        CheckWorkflow.run,
        CheckInput(check_id="c1", query="Acme"),
        id=f"wf-{uuid.uuid4()}",
        task_queue="test",
    )
    return worker, handle


async def _wait_status(handle: WorkflowHandle, status: str) -> None:
    for _ in range(200):
        if (await handle.query(CheckWorkflow.state))["status"] == status:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"status never became {status!r}")


async def test_question_answered_once_despite_double_click(env):
    async def script(n, inp):
        if "q1" not in inp.answers:
            return RunAgentResult(status="waiting", questions=[QUESTION])
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_status(handle, "waiting_for_input")
        same = str(uuid.uuid4())
        for cmd in (same, same, str(uuid.uuid4())):  # retry + a second click
            await handle.signal(CheckWorkflow.answer, AnswerSignal(cmd, "q1", "cmp_001"))
        await handle.signal(CheckWorkflow.answer, AnswerSignal(str(uuid.uuid4()), "nope", "x"))
        await _wait_status(handle, "completed")

    answers = [e for e in fakes.events if e.type == "answer"]
    assert [a.payload for a in answers] == [{"question_id": "q1", "value": "cmp_001"}]
    assert fakes.runs[-1].answers == {"q1": "cmp_001"}
    assert fakes.statuses() == ["running", "waiting_for_input", "running", "completed"]


async def test_pause_cancels_agent_and_resume_reruns_it(env):
    started = asyncio.Event()

    async def script(n, inp):
        if n == 1:  # long-running first attempt: heartbeat until cancelled by pause
            started.set()
            while True:
                activity.heartbeat()
                await asyncio.sleep(0.05)
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await started.wait()
        await handle.signal(CheckWorkflow.pause, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "paused")
        await handle.signal(CheckWorkflow.resume, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "completed")

    assert len(fakes.runs) == 2
    assert fakes.statuses() == ["running", "paused", "running", "completed"]


async def test_answer_while_paused_is_applied_on_resume(env):
    async def script(n, inp):
        if "q1" not in inp.answers:
            return RunAgentResult(status="waiting", questions=[QUESTION])
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_status(handle, "waiting_for_input")
        await handle.signal(CheckWorkflow.pause, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "paused")
        await handle.signal(CheckWorkflow.answer, AnswerSignal(str(uuid.uuid4()), "q1", "a"))
        await asyncio.sleep(0.3)
        assert len(fakes.runs) == 1  # nothing runs while paused
        await handle.signal(CheckWorkflow.resume, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "completed")

    assert fakes.runs[-1].answers == {"q1": "a"}


async def test_failure_waits_for_retry(env):
    async def script(n, inp):
        if n == 1:
            raise ApplicationError("LLM returned garbage", non_retryable=True)
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_status(handle, "error")
        await handle.signal(CheckWorkflow.retry, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "completed")

    assert fakes.statuses() == ["running", "error", "running", "completed"]
    error = next(e for e in fakes.events if e.payload.get("status") == "error")
    assert "garbage" in error.payload["detail"]
    assert error.payload["message"].startswith("The agent hit an unexpected error")


async def test_survives_a_pause_of_many_hours(env):
    async def script(n, inp):
        if "q1" not in inp.answers:
            return RunAgentResult(status="waiting", questions=[QUESTION])
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_status(handle, "waiting_for_input")
        await handle.signal(CheckWorkflow.pause, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "paused")
        await env.sleep(timedelta(hours=8))  # time-skipping: no timeouts fire while paused
        await handle.signal(CheckWorkflow.answer, AnswerSignal(str(uuid.uuid4()), "q1", "a"))
        await handle.signal(CheckWorkflow.resume, ControlSignal(str(uuid.uuid4())))
        await _wait_status(handle, "completed")

    assert fakes.statuses()[-1] == "completed"


async def _wait_runs(fakes: Fakes, n: int) -> None:
    for _ in range(200):
        if len(fakes.runs) >= n:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"only {len(fakes.runs)} runs, expected {n}")


async def test_feedback_sent_while_running_is_queued_and_handled_in_order(env):
    release = asyncio.Event()

    async def script(n, inp):
        if n == 1:
            await release.wait()  # first check still running when feedback arrives
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_runs(fakes, 1)
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        await handle.signal(CheckWorkflow.feedback, FeedbackSignal(a, "leave out n1"))
        await handle.signal(CheckWorkflow.feedback, FeedbackSignal(a, "leave out n1"))  # retry
        await handle.signal(CheckWorkflow.feedback, FeedbackSignal(b, "last 12 months"))
        release.set()
        await _wait_runs(fakes, 3)
        await _wait_status(handle, "completed")
        state = await handle.query(CheckWorkflow.state)

    assert [r.feedback and r.feedback["text"] for r in fakes.runs] == [
        None,
        "leave out n1",
        "last 12 months",
    ]
    assert state["feedback_queue"] == []
    feedback_events = [e for e in fakes.events if e.type == "feedback"]
    assert [e.key for e in feedback_events] == [f"feedback:{a}", f"feedback:{b}"]
    assert all(e.payload["queued"] for e in feedback_events)


async def test_feedback_round_can_ask_a_question(env):
    async def script(n, inp):
        if inp.feedback and "q1" not in inp.answers:
            return RunAgentResult(status="waiting", questions=[QUESTION])
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_status(handle, "completed")
        await handle.signal(CheckWorkflow.feedback, FeedbackSignal(str(uuid.uuid4()), "hm"))
        await _wait_status(handle, "waiting_for_input")
        await handle.signal(CheckWorkflow.answer, AnswerSignal(str(uuid.uuid4()), "q1", "a"))
        await _wait_runs(fakes, 3)
        await _wait_status(handle, "completed")

    assert fakes.runs[-1].answers == {"q1": "a"}
    assert fakes.runs[-1].feedback["text"] == "hm"


async def test_long_sessions_continue_as_new(env):
    rounds = workflows.ROUNDS_PER_RUN + 2

    async def script(n, inp):
        return RunAgentResult(status="done")

    fakes = Fakes(script)
    worker, handle = await _start(env, fakes)
    async with worker:
        await _wait_status(handle, "completed")
        ids = [str(uuid.uuid4()) for _ in range(rounds)]
        for i, cmd in enumerate(ids):
            await handle.signal(CheckWorkflow.feedback, FeedbackSignal(cmd, f"round {i}"))
        await _wait_runs(fakes, rounds + 1)
        await _wait_status(handle, "completed")
        # A command from before the continue-as-new is still recognised as a repeat.
        await handle.signal(CheckWorkflow.feedback, FeedbackSignal(ids[0], "round 0"))
        await asyncio.sleep(0.3)
        desc = await env.client.get_workflow_handle(handle.id).describe()

    assert [r.feedback["text"] for r in fakes.runs[1:]] == [f"round {i}" for i in range(rounds)]
    assert len(fakes.runs) == rounds + 1
    assert desc.run_id != handle.first_execution_run_id  # continued as a new run
