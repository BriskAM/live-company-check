# Live Company Check

An AI assistant that runs a company due-diligence check together with a human reviewer:
live streaming, interactive questions, pause/resume, feedback rounds and inline charts.

**Stack:** Python 3.12 + FastAPI · LangGraph · Temporal · PostgreSQL · Next.js · Gemini

> Work in progress. See [docs/PLAN.md](docs/PLAN.md) for the design and build order,
> [docs/DECISIONS.md](docs/DECISIONS.md) for decisions and trade-offs, and
> [docs/PROGRESS.md](docs/PROGRESS.md) for status and bugs found.

## Running locally

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env            # add your GOOGLE_API_KEY
docker compose up -d            # Postgres (5433) + Temporal (7233, UI on 8233)

cd backend
uv sync
uv run alembic upgrade head     # create tables
uv run python -m company_check.seed   # load companies (sample_checks are never loaded)

uv run uvicorn company_check.api.app:app --reload --port 8000   # API
uv run python -m company_check.worker.main                      # Temporal worker (2nd terminal)

cd ../frontend
npm install
npm run dev                     # http://localhost:3000
```

Health check: `curl localhost:8000/health` → `{"database":"ok","temporal":"ok"}`.

## Scenario scripts

With the API and worker running:

```bash
cd backend
uv run python scripts/pause_mid_report.py "Copperleaf Software"   # pause mid-report, resume, check the seam
uv run python scripts/verify_stream.py <check_id>                 # visible text == final report?
uv run python scripts/feedback.py <check_id> "Only show the last 12 months" --target chart:chart-1
uv run python scripts/feedback.py <check_id> "<same text>" --times 2     # repeat → no-op
uv run python -m company_check.cli "Brightline Logistics"         # run the agent in the terminal
```

## Repository layout

```
backend/    FastAPI API, Temporal worker (workflows + activities), LangGraph agent, Alembic
frontend/   Next.js reviewer UI
data/       dummy_data.json (seed data)
docs/       plan, decisions, architecture
```
