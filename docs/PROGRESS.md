# Progress Log

## Milestones

| # | Milestone | Status |
|---|---|---|
| M0 | Skeleton: Docker Compose (Postgres, Temporal), FastAPI, worker, Next.js, migrations, seed | Done |
| M1 | Data tools + LangGraph agent, CLI runner | Done |
| M2 | Temporal workflow, event log, SSE, live UI | Done |
| M3 | Questions (single / multi / text), idempotent answers | Done (with M2) |
| M4 | Pause mid-writing, restart survival, long pauses | Done |
| M5 | Visualization tools (4), inline charts/tables, update-by-id | Done |
| M6 | Feedback rounds: queue, patch tools, selective recompute, repeat dedupe, continue-as-new | Done |
| M7 | Failure polish, full Compose stack, README, architecture diagrams | Done |
| M8 | Frontend: split view (activity + case file), light/dark themes after amehta.space, progress steps, mobile tabs | Done |
| M9 | Pre-demo: request limiter, token and cost estimate, sample-check runner, fresh-clone Docker test, automated demo video | Done |
| Demo | Automated video (`demo/`), narrated plan (`docs/DEMO.md`) | Done |

## Sample checks status

All five are driven end to end by `scripts/run_samples.py` (26/26 expectations met on
2026-11-01, about 6 minutes in all with the request limiter on).

| Check | Status |
|---|---|
| 1 Brightline | Single-choice lookalike question. "I meant the Inc one" restarts the check for cmp_002 |
| 2 Northwind | Agent already ignores the clinic articles. Exclusion feedback applied, report left alone (nothing it reads changed). Timeline on request. Same feedback twice → both no-ops, no LLM call for the repeat |
| 3 Pinecrest | Rating drop charted unprompted with the sale marked, plus a before/after table. "Last 12 months", remove marker, "mark the sale date" each update chart-1 in place, range kept. Table split into three periods from the UI |
| 4 Copperleaf | Leafline data exposure found. Pause mid-report + resume continues the text. Worker/API restart. Long pause across Temporal restart |
| 5 Harborview | Multi-choice → free-text → focused findings. Long session tested on Pinecrest: 13 report rounds, 10 sent in one burst (queued, handled in order), continued-as-new after 8 rounds |

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
10. **Excluding an uncited article rewrote the report anyway.** The report only reads the
    reviews and the cited evidence, so now only excluding a review or changing a concern
    marks it for a rewrite.
11. **A later report rewrite undid an earlier request** ("put the recommendation in bold"
    was lost three rounds later). Wording requests are now kept as standing instructions for
    every rewrite; later ones win on conflict.
12. **Repeat of an exclusion after other feedback went to the LLM** (harmless, but a wasted
    call). A repeat is now also a no-op when the earlier identical feedback only excluded
    evidence and all of it is still excluded.
13. **A chart "update" that changed nothing created a new version.** Now skipped.
14. **Errors showed raw exception text** (long Gemini payloads). Errors are now mapped to a
    plain message (rate limit, overload, key/billing, timeout) with the raw text, shortened,
    behind "Details".
15. **Hitting the analyst turn limit was retried 4 times**, failing the same way each time.
    At the limit the analyst is now made to finish (only `submit_findings` offered, or a
    plain reply in a feedback round); if it still can't, the error is not retried.
16. **`docker stop` would kill the worker abruptly** (SIGTERM wasn't handled). The worker
    now shuts down gracefully, giving running agents 5 s.
17. **Mobile layout overflowed sideways** (a grid column sized itself to its widest row).
    Fixed with a `minmax(0, 1fr)` column; checked at 375 px.
18. **Dracula's purple failed the chart lightness check on the dark card** (too light for a
    line series). Charts use a validated `#a07af0` in dark mode, `#8250df` in light.

19. **Bursts of feedback went over Flash-Lite's 15 requests a minute** (20 RPM peak on the
    quota page), causing 429s and fallbacks. A client-side limiter now spaces calls (14/min).
20. **Report numbers differed between sections** (2.5 vs 2.63 after the sale). The report
    now gets the reviewer-visible period tables recomputed and must reuse those figures,
    naming the period for every average.
21. **"Check complete" repeated after every feedback round**; only the first is shown now,
    and the header progress shows a "Feedback round" step while one runs.

22. **A fresh clone failed to build the frontend image**: the Dockerfile copied `public/`,
    which is empty and so not in git. Found by cloning the public repo into an empty folder
    and running `docker compose --profile app up --build`; the copy is removed.
23. **Repeating an exclusion called the LLM again when the exclusion round had also edited
    concerns** (the agent dropped a concern and changed the recommendation with
    `update_findings`). The no-op rule now accepts those follow-on edits.

24. **Charts were only in the case file after the M8 redesign**, while they belong
    inline in the conversation where the agent made them. Each chart version is drawn
    in the activity stream again (older versions dimmed, with a link to the latest); the
    case file keeps the current one.

## Verified scenarios (live, Gemini)

| Scenario | Result |
|---|---|
| Pause 159 chars into the report, resume | Report continued mid-sentence; no repeat (`scripts/pause_mid_report.py`) |
| `kill -9` worker 1 s into the report + restart API | Retried activity continued the text after ~14 s; visible text == final report (`scripts/verify_stream.py`) |
| Pause while waiting for an answer, restart Temporal + worker + API, answer while paused, resume | Question and pause survived; answer applied; completed |
| Check failed with 402, then Retry after fixing the key | Resumed from checkpoint and completed |
| 8 h pause | Workflow test on the time-skipping server |
| Feedback rounds (Northwind, Pinecrest, Brightline) | See sample checks table; driven with `scripts/feedback.py` and from the UI |
| SIGTERM to the worker while the report streams | Graceful shutdown let the run finish; visible report == final report |
| `kill -9` of the worker in a feedback round | "Recovering from a temporary problem (attempt 2)" shown; round and the queued next one completed |
| Full stack via `docker compose --profile app up` | Migrate + seed, check + feedback round, UI all from containers |
| Fresh clone of the public repo + `.env`, `docker compose --profile app up --build` | Built and seeded after fix 22; `run_samples.py` against the containers 24/26, then 26/26 after fix 23 |
| `scripts/run_samples.py` (all five sample checks) | 26/26 pass, including a pause caught from the live stream and 11 report rounds in a row |
| 10 feedback messages in one burst | Queued, handled one by one, workflow continued as new after 8 rounds; dedupe of old command ids survives it (workflow test) |

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
- Feedback sent while the agent is busy shows the agent's reply to an earlier feedback
  below it (events are shown in the order they happened). Clear enough with the
  "Queued" / "Applied" note under each message, and the case file always shows the
  current state.
- On resume after a question, the graph re-emits the step's events; idempotency keys drop
  them in the DB, the console runner still prints them (expected).
- `docker compose up -d` still starts only Postgres and Temporal (for development on the
  host); the full stack is behind the `app` profile.

## Environment notes
- Postgres on host port 5433, Temporal on 7233 (UI 8233), API 8000, UI 3000.
- Temporal dev server persists to a SQLite file on a volume.
- Python 3.12 via uv; Node 26; Next.js 15.5; LangGraph 1.2; temporalio 1.35.
