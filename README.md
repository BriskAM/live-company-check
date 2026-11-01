# Live Company Check

An AI assistant that runs a company due-diligence check together with a human reviewer:
live streaming, interactive questions, pause/resume, feedback rounds and inline charts.

**Stack:** Python 3.11 + FastAPI · LangGraph · Temporal · PostgreSQL · Next.js

> Work in progress — setup and run instructions will be added as the system is built.

## Repository layout

- `data/dummy_data.json` — seed data (companies only are loaded; `sample_checks` are used for tests, never by the agent)
- `docs/` — architecture notes, decisions and trade-offs
