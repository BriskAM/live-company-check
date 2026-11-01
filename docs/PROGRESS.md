# Progress Log

## Milestones

| # | Milestone | Status |
|---|---|---|
| M0 | Skeleton: Docker Compose (Postgres, Temporal), FastAPI, worker, Next.js, migrations, seed | ✅ done |
| M1 | Data tools + LangGraph agent, CLI runner | ✅ done |
| M2 | Temporal workflow, event log, SSE, live UI | ✅ done |
| M3 | Questions (single / multi / text), idempotent answers | ✅ done (with M2) |
| M4 | Pause mid-writing, restart survival, long pauses | ✅ done |
| M5 | Visualization tools (4), inline charts/tables, update-by-id | ✅ done (chart *feedback* arrives with M6's message path) |
| M6 | Feedback rounds with selective recompute | ⏳ |
| M7 | Failure polish, README, architecture diagram, demo | ⏳ |

## Sample checks status

| Check | Status |
|---|---|
| 1 Brightline | Single-choice lookalike question ✅. Monthly chart (M5), switch to Inc (M6) pending |
| 2 Northwind | Agent already excludes the clinic articles on its own ✅. Feedback flow (M6) pending |
| 3 Pinecrest | Rating drop found ✅. Agent charts it unprompted with the sale marked, plus a before/after table ✅. Chart feedback (M6) pending |
| 4 Copperleaf | Leafline data exposure found ✅. Pause mid-report + resume continues the text ✅. Worker/API restart ✅. Long pause across Temporal restart ✅ |
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
8. **Billing errors were retried 4 times.** A 402 "prepayment credits are depleted" can't fix
   itself. Permanent LLM errors (401/402/403, invalid key) now fail fast as non-retryable;
   the check shows the error and waits for Retry. 429/503/timeouts still retry.
9. **Agent's before/after table counted every review as "after".** It left the second
   period unbounded. Periods are now validated (bounded, non-overlapping, month-only dates
   normalised) and a bad call goes back to the model, which then fixed it.

## Verified scenarios (live, Gemini)

| Scenario | Result |
|---|---|
| Pause 159 chars into the report, resume | Report continued mid-sentence; no repeat (`scripts/pause_mid_report.py`) |
| `kill -9` worker 1 s into the report + restart API | Retried activity continued the text after ~14 s; visible text == final report (`scripts/verify_stream.py`) |
| Pause while waiting for an answer, restart Temporal + worker + API, answer while paused, resume | Question and pause survived; answer applied; completed |
| Check failed with 402, then Retry after fixing the key | Resumed from checkpoint and completed |
| 8 h pause | Workflow test on the time-skipping server |

## Observations / open issues

- **Free-tier rate limits (429) on 3.5 Flash** were hit on 2026-11-01 after ~30 calls in a
  short window; the Flash-Lite fallback handled every one. A paid key is being arranged.
- **Second API key (2026-11-01) is on a prepaid project with no credit** → every model
  returns 402. Back on the free key; primary model switched to **3.5 Flash-Lite** (lasts
  longer on free quota), falling back to 3.5 Flash.
- **Gemini streams in large chunks** (~150–250 chars per chunk), so the "mid-writing" window
  is a couple of seconds. The longer report (Customer reviews section) widens it.
- Analyst messages (1–3 sentences before tool calls) are restarted, not continued, after an
  interruption — continuing a half-written tool-calling turn is not reliable. Only the
  report, which is the long text, is continued.
- On resume after a question, the graph re-emits the step's events; idempotency keys drop
  them in the DB, the console runner still prints them (expected).
- The worker and API run on the host during development; Compose only runs Postgres and
  Temporal. Full Compose stack is planned for M7.

## Environment notes
- Postgres on host port 5433, Temporal on 7233 (UI 8233), API 8000, UI 3000.
- Temporal dev server persists to a SQLite file on a volume.
- Python 3.12 via uv; Node 26; Next.js 15.5; LangGraph 1.2; temporalio 1.35.
