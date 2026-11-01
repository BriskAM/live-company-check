# AGENTS.md

Guide for coding agents working on this repository. Read this first, then `docs/DECISIONS.md`
for the why behind the design and `docs/PROGRESS.md` for history, bugs fixed and open issues.

## What this is

**Live Company Check**: an AI agent that checks a company (records, customer reviews, news)
while a human reviewer watches live, answers its questions, pauses it and gives feedback.
A personal project. Stack:
Python/FastAPI, LangGraph, Temporal, PostgreSQL, Next.js. LLM: Google Gemini (free tier).

## Ground rules

- **No AI attribution in commits or PRs.** No `Co-Authored-By` lines, no "Generated with"
  footers. The owner has asked for this explicitly.
- **Never commit `.env`** or print the API key. Settings live in `.env` (see `.env.example`).
- **`data/dummy_data.json` → `sample_checks` are test-only.** They describe expected results.
  Never load them into the database, the agent's tools or its prompts. Only
  `backend/scripts/run_samples.py` reads them. `seed.py` loads companies only.
- **The repo is public.** Keep private notes (.docx/.pdf) and the `private/` folder out of it.
- The agent may only see what its tools return, never the raw data file.
- Keep the style of the surrounding code: short docstrings that say why, few comments, plain
  names. Docs use plain English, no emojis.
- Update `docs/PROGRESS.md` (bugs found and fixed, verified scenarios) and
  `docs/DECISIONS.md` (what, why, cost) when you change behaviour.

## Layout

```
backend/src/company_check/
  api/app.py, schemas.py   FastAPI: create checks, commands (answers, feedback, pause, resume, retry), SSE events
  worker/workflows.py      Temporal workflow per check: control flow, signals, feedback queue, continue-as-new
  worker/activities.py     run_agent activity: runs the LangGraph graph with the Postgres checkpointer
  worker/errors.py         raw errors → reviewer-friendly message + detail
  worker/main.py           worker process (graceful shutdown on SIGTERM)
  agent/graph.py           LangGraph graph: resolve → gather → analyst ⇄ tools → report → finish_round; feedback rounds
  agent/toolbox.py         tool handlers + registry (HANDLERS, SCHEMAS, CHECK_/FEEDBACK_TOOL_NAMES, CHARTS)
  agent/state.py           graph state + tool argument schemas (docstrings are what the model reads)
  agent/prompts.py         system prompts, dossier, feedback round context
  agent/llm.py             the only place models are built: Gemini, fallback, per-minute limiter
  agent/viz.py, stats.py   chart specs and statistics, pure functions over state
  agent/continuation.py    joining a resumed report onto text already shown
  agent/data.py            data access for tools (search, company, reviews, news)
  event_store.py           append-only check_events log (per-check seq, idempotency keys, NOTIFY)
  models.py, db.py, pg.py  SQLAlchemy models, sessions, psycopg pool
  seed.py                  loads companies from data/dummy_data.json
backend/migrations/        Alembic
backend/tests/             pytest (never calls the LLM)
backend/scripts/           run_samples, feedback, pause_mid_report, verify_stream, reset_demo
frontend/src/
  lib/checkState.ts        the UI is a pure reducer over the event log (+ checkState.test.ts)
  lib/useCheckEvents.ts    EventSource subscription with replay
  lib/api.ts, pricing.ts   API client; cost estimate per model
  app/checks/[id]/page.tsx check page: header, activity stream (left), case file (right)
  components/              Timeline, CaseFile, ChartBlock, QuestionCard, FeedbackBox, ...
demo/                      local demo tooling (ignored; keep private)
private/                   private notes, editable diagram sources (ignored)
docs/                      ARCHITECTURE (rendered diagrams), DECISIONS, PROGRESS, PLAN
```

## Run it

Prerequisites: Docker, Python 3.12 with `uv`, Node 22+. Copy `.env.example` to `.env` and set
`GOOGLE_API_KEY`.

```bash
docker compose up -d                       # Postgres (5433) + Temporal (7233, UI 8233)
cd backend && uv sync
uv run alembic upgrade head && uv run python -m company_check.seed
uv run uvicorn company_check.api.app:app --reload --port 8000    # API
uv run python -m company_check.worker.main                       # worker, separate terminal
cd ../frontend && npm install && npm run dev                     # UI on :3000
```

Everything in containers instead: `docker compose --profile app up --build` (UI :3000, API :8000).

**Restart the worker after any backend change under `agent/` or `worker/`.** The API reloads
itself with `--reload`; the worker does not.

## Check your work

```bash
cd backend && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm test && npx tsc --noEmit && npm run lint
cd backend && uv run python scripts/run_samples.py --quick      # live LLM, all five sample checks
```

`run_samples.py` should report 26/26. It uses real LLM calls (free tier: about 15 requests
a minute, a few hundred a day), so run single checks (`run_samples.py check_3`) while iterating.

## Common tasks

- **New agent tool:** schema in `agent/state.py`, handler in `agent/toolbox.py` (raise
  `ToolError` for bad input; add evidence via the turn helpers), register in `HANDLERS` and
  `SCHEMAS`, add to `CHECK_TOOL_NAMES` and/or `FEEDBACK_TOOL_NAMES`, give it a label in
  `frontend/src/components/Timeline.tsx`.
- **New chart:** schema + `CHARTS` entry + branch in `build_spec` + pure function in
  `agent/viz.py` + renderer for the spec `type` in `frontend/src/components/ChartBlock.tsx`.
- **New event type:** `sink.emit(type, payload, key=...)` (key = idempotency) and a case in
  `reduce()` in `frontend/src/lib/checkState.ts` with a test.
- **Change model or provider:** `.env` (`LLM_MODEL`, `LLM_FALLBACK_MODEL`, `LLM_RPM`,
  `LLM_FALLBACK_RPM`) or `agent/llm.py`. Add prices to `frontend/src/lib/pricing.ts`.
- **Workflow change:** keep it deterministic (no I/O, time or randomness in workflow code);
  add a test in `tests/test_workflow.py` (time-skipping test server, fake activities).
  Changing workflow code breaks replay of running checks: run `scripts/reset_demo.py --yes`
  locally if old checks get stuck.
- **Record the demo video:** local instructions are in `private/DEMO.md`; demo tooling,
  recordings and editable Excalidraw sources must stay ignored. Recording clears all checks.
- **Clear checks before a demo:** `cd backend && uv run python scripts/reset_demo.py --yes`.

## Gotchas

- Gemini streams in large chunks; the "mid-report" pause window is a second or two.
- The free tier is the main limit: 429s fall back to the second model, then Temporal retries.
- Ports: Postgres 5433 (not 5432), Temporal 7233, Temporal UI 8233, API 8000, UI 3000.
- The case file shows the latest version of everything; the activity stream is the history
  (each chart version inline where it was made).
- `docker compose up -d` without `--profile app` starts only Postgres and Temporal.
