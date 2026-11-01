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

from company_check.worker.types import (
    AnswerSignal,
    CheckInput,
    ControlSignal,
    EmitInput,
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
        await handle.result()

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
        await handle.result()

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
        await handle.result()

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
        await handle.result()

    assert fakes.statuses() == ["running", "error", "running", "completed"]
    error = next(e for e in fakes.events if e.payload.get("status") == "error")
    assert "garbage" in error.payload["message"]
