# Live Company Check

An AI assistant that runs a company due-diligence check together with a human reviewer:
live streaming, interactive questions, pause/resume, feedback rounds and inline charts.

**Stack:** Python 3.12 + FastAPI, LangGraph, Temporal, PostgreSQL, Next.js, Gemini

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, agent graph, workflow states, failures
- [docs/DECISIONS.md](docs/DECISIONS.md): decisions and trade-offs
- [docs/PROGRESS.md](docs/PROGRESS.md): status, bugs found, verified scenarios
- [docs/PLAN.md](docs/PLAN.md): the original plan and build order

## What it does

| Requirement | How |
|---|---|
| Live streaming | Every step is an event in Postgres, streamed over SSE; refresh or reconnect replays the same view |
| Questions | Single choice (ambiguous name), multiple choice and free text; answered once even on double click |
| Pause / resume | Pause stops the agent within about a second, mid-sentence if need be; resume continues the text. Survives restarts of the API, worker and Temporal, and pauses of hours |
| Feedback rounds | Send feedback any time (queued while busy); only work it affects is redone; repeats are no-ops; long sessions continue as new |
| Visualization | Rating trend, news timeline, ratings by period, company comparison; computed from data, updated in place by feedback ("last 12 months", "mark the sale date") |
| Failures | Retries with backoff, model fallback, plain error messages with Retry, graceful worker shutdown |

```mermaid
flowchart LR
    UI[Next.js UI] -- commands --> API[FastAPI]
    API -- signals --> WF[Temporal workflow]
    WF -- runs --> AG[LangGraph agent<br/>in a worker activity]
    AG -- events, checkpoints, data --> PG[(PostgreSQL)]
    AG -- streams --> LLM[Gemini]
    PG -- SSE --> UI
```

## Quick start (everything in Docker)

Prerequisites: Docker and a Google AI Studio API key.

```bash
cp .env.example .env                 # set GOOGLE_API_KEY
docker compose --profile app up -d --build
```

Open http://localhost:3000. The API is on 8000 and the Temporal UI on 8233. The `migrate`
service creates the tables and loads the companies from `data/dummy_data.json` (the
`sample_checks` section is never loaded).

## Development setup

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env            # add your GOOGLE_API_KEY
docker compose up -d            # Postgres (5433) + Temporal (7233, UI on 8233) only

cd backend
uv sync
uv run alembic upgrade head     # create tables
uv run python -m company_check.seed   # load companies

uv run uvicorn company_check.api.app:app --reload --port 8000   # API
uv run python -m company_check.worker.main                      # worker (2nd terminal)

cd ../frontend
npm install
npm run dev                     # http://localhost:3000
```

Health check: `curl localhost:8000/health` returns `{"database":"ok","temporal":"ok"}`.

## Tests

```bash
cd backend && uv run pytest     # needs Postgres running and seeded; never calls the LLM
cd frontend && npm test && npx tsc --noEmit && npm run lint   # reducer tests: replay, reconnect, restarts
```

Workflow tests run on Temporal's time-skipping test server with fake activities (questions,
double clicks, pause and resume, answers while paused, an 8-hour pause, retry after failure,
queued feedback, continue-as-new). Agent pieces are tested without the LLM; the agent itself
is exercised live with the scripts below.

## Scenario scripts

With the API and worker running:

```bash
cd backend
uv run python scripts/pause_mid_report.py "Copperleaf Software"   # pause mid-report, resume, check the seam
uv run python scripts/verify_stream.py <check_id>                 # visible text == final report?
uv run python scripts/feedback.py <check_id> "Only show the last 12 months" --target chart:chart-1
uv run python scripts/feedback.py <check_id> "<same text>" --times 2     # repeat → no-op
uv run python -m company_check.cli "Brightline Logistics"         # run the agent in the terminal
uv run python scripts/run_samples.py [check_3] [--quick]          # all five sample checks, PASS/FAIL per step
uv run python scripts/reset_demo.py --yes                          # clear every check before a demo
```

`run_samples.py` reads the sample checks from `data/dummy_data.json` itself; they never reach
the database or the agent. It uses the real LLM and takes several minutes on the free tier.

## Extending it

**A new data tool** (for example court records):
1. A Pydantic args schema in `agent/state.py`; its docstring is what the model reads.
2. An async handler `(turn, args, call_id)` in `agent/toolbox.py`. Read data through
   `agent/data.py`, add new evidence with `turn.add(...)`, and raise `ToolError` for bad input
   so the model sees the error and can try again.
3. Register it in `HANDLERS` and `SCHEMAS`, and add it to `CHECK_TOOL_NAMES` and/or
   `FEEDBACK_TOOL_NAMES`. Add a label for the activity stream in `frontend/src/components/Timeline.tsx`.

**A new chart:** a schema, a `CHARTS` entry (name, schema, default title), a branch in
`build_spec` that returns a spec with a `type`, the pure data function in `agent/viz.py` (so
`rebuild_charts` can recompute it after exclusions), and a renderer for that `type` in
`frontend/src/components/ChartBlock.tsx`. Versioning, feedback targeting and replay work
without changes.

**A new event type:** emit it with `sink.emit(type, payload, key=...)` (the key makes retries
idempotent) and handle it in `reduce()` in `frontend/src/lib/checkState.ts`, with a test in
`checkState.test.ts`. Nothing in between needs to know about it.

**Another LLM provider:** `agent/llm.py` is the only place a model is built. Return any
LangChain chat model that supports tool calling and streaming from `_base_model`.

## Repository layout

```
backend/    FastAPI API, Temporal worker (workflows + activities), LangGraph agent, Alembic
frontend/   Next.js reviewer UI
data/       dummy_data.json (seed data)
docs/       architecture, decisions, progress, plan
```
