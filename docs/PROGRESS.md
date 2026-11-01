# Progress Log

## Milestones

| # | Milestone | Status |
|---|---|---|
| M0 | Skeleton: Docker Compose (Postgres, Temporal), FastAPI, worker, Next.js, migrations, seed | ✅ done |
| M1 | Data tools + LangGraph agent, CLI runner | ✅ done |
| M2 | Temporal workflow, event log, SSE, live UI | ✅ done |
| M3 | Questions (single / multi / text), idempotent answers | ✅ done (with M2) |
| M4 | Pause mid-writing, restart survival, long pauses | 🚧 in progress |
| M5 | Visualization tools + chart feedback | ⏳ |
| M6 | Feedback rounds with selective recompute | ⏳ |
| M7 | Failure polish, README, architecture diagram, demo | ⏳ |

## Sample checks status

| Check | Status |
|---|---|
| 1 Brightline | Single-choice lookalike question ✅. Monthly chart (M5), switch to Inc (M6) pending |
| 2 Northwind | Agent already excludes the clinic articles on its own ✅. Feedback flow (M6) pending |
| 3 Pinecrest | Rating drop found ✅. Charts (M5) pending |
| 4 Copperleaf | Leafline data exposure found ✅. Pause mid-output + restart (M4) in progress |
| 5 Harborview | Multi-choice → free-text → focused findings ✅. Feedback rounds (M6) pending |

## Bugs found and fixed

1. **Trigram `matched_on` always said "previous_name".** Postgres `GREATEST(real, numeric)`
   returns double precision, which never equals the `real` similarity. Now computed in
   Python from the two values.
2. **Alembic autogenerate dropped LangGraph's checkpoint tables.** They are not in our models.
   Added an `include_object` filter so Alembic only manages our tables.
3. **Pause didn't stop the agent.** Temporal throttles heartbeats to 80% of the heartbeat
   timeout (12 s) and cancellation is only delivered in a heartbeat response; a short run
   finished before the cancel arrived. Heartbeat throttle set to 1 s → pause stops the agent
   in ~1 s.
4. **Resume right after pause was treated as an error.** The loop checked the current
   `paused` flag, which a quick resume had already cleared. Now a flag records that the
   cancellation came from a pause.
5. **Pausing a finished check returned 500.** Signals to a completed workflow now return 409.
6. **Harborview never asked the free-text follow-up.** The prompt made it optional; made it a
   firm step after the reviewer picks sources in a conflict.
7. **Ruff picked up a global config** (B008 flags FastAPI's `Depends`). Project now pins its
   own lint rules.

## Observations / open issues

- **Free-tier rate limits (429) on 3.5 Flash** were hit on 2026-11-01 after ~30 calls in a
  short window; the Flash-Lite fallback handled every one. A paid key is being arranged.
- **Gemini streams in large chunks** (a 300-word report arrives in ~7 chunks within ~2 s), so
  the "mid-writing" window for pause is short. Addressed in M4.
- On resume after a question, the graph re-emits the step's events; idempotency keys drop
  them in the DB, the console runner still prints them (expected).
- The worker and API run on the host during development; Compose only runs Postgres and
  Temporal. Full Compose stack is planned for M7.

## Environment notes
- Postgres on host port 5433, Temporal on 7233 (UI 8233), API 8000, UI 3000.
- Temporal dev server persists to a SQLite file on a volume.
- Python 3.12 via uv; Node 26; Next.js 15.5; LangGraph 1.2; temporalio 1.35.
