"""Temporal workflow: one per check. Owns control flow (running / paused / waiting).

Skeleton for M0 — the agent loop, signals and pause/resume are added in M2–M4.
Workflow code must stay deterministic: no I/O, no LLM calls, no wall-clock reads here;
all of that happens in activities.
"""

from dataclasses import dataclass

from temporalio import workflow


@dataclass
class CheckInput:
    check_id: str
    company_query: str


@workflow.defn
class CheckWorkflow:
    @workflow.run
    async def run(self, inp: CheckInput) -> str:
        workflow.logger.info("check %s started for %r", inp.check_id, inp.company_query)
        return "started"
