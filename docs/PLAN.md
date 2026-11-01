# Implementation Plan — Live Company Check

Target: ~5–7 focused hours. Priority is a small system that is **correct under failure**
(refresh, restart, double-click, pause mid-stream, LLM errors) over a large feature list.

## 1. Architecture at a glance

```
 Next.js UI ──REST (commands)──▶ FastAPI ──signals/start──▶ Temporal server
     ▲                              │                            │
     └──── SSE (event stream) ◀─────┤                            ▼
                                    │                     Temporal worker
                                    │              (workflow + activities;
                                    │               LangGraph runs inside
                                    ▼               activities)
                               PostgreSQL ◀──────────────────────┘
        companies/reviews/news · checks · check_events · commands · LangGraph checkpoints
```

**Responsibilities — one owner per concern:**

| Concern | Owner | Why |
|---|---|---|
| Control flow: running / paused / waiting for answer / waiting for feedback | **Temporal workflow** (one per check) | Durable; survives API + worker restarts; signals and `wait_condition` can wait for hours for free |
| Agent reasoning (tool calls, concerns, report) | **LangGraph** graph, executed inside activities | Non-deterministic LLM work must not live in workflow code |
| Agent state between steps | **LangGraph Postgres checkpointer** | Resume continues from the last completed node, not from scratch |
| What the reviewer sees | **`check_events` table** (append-only, per-check `seq`) | Single source for live streaming *and* replay after refresh |
| Domain data | Postgres tables `companies`, `previous_names`, `reviews`, `news` | Agent only sees tool results, never the raw file |

## 2. Key flows

### Streaming (req. 04, edge case "refresh")
- Activities write events (`step_started`, `tool_call`, `tool_result`, `token_delta`,
  `chart`, `question`, `report_section`, `status`) to `check_events(check_id, seq, type, payload)`.
- Token deltas are **buffered and flushed every ~100 ms** → few rows, still feels live.
- After insert: `NOTIFY check_<id>`. FastAPI SSE endpoint: replay `seq > Last-Event-ID`,
  then `LISTEN`. Browser `EventSource` reconnects with `Last-Event-ID` automatically
  → **no gaps, no duplicates** on refresh.
- UI state = pure fold over the event list (reducer), so replay and live are the same code path.

### Questions (req. 05)
- Agent calls an `ask_reviewer` tool (`single` | `multi` | `text`, options) → LangGraph `interrupt()`.
- Activity returns `NeedsInput(question_id)`; workflow emits `question` event and
  `wait_condition(answer received)` — can wait hours, survives restarts.
- Answer = `POST /checks/{id}/answers` with `question_id` → signal. Workflow accepts the
  **first** answer per `question_id`, ignores the rest (double-click safe). UI disables after click.

### Pause / resume (req. 06)
- Pause = signal. The running activity **heartbeats** while streaming; workflow cancels it.
  Activity catches cancellation, flushes partial text, marks the message `interrupted`.
- Resume = signal → workflow re-runs the activity from the last LangGraph checkpoint.
  Report is generated **per section** (summary, concerns, open questions, recommendation)
  as separate nodes, so at most the current section is regenerated; the partial text of
  that section is passed back so the model continues rather than rewrites.
- Answer arrives while paused → stored, applied on resume.

### Feedback (req. 07)
- Agent state is structured, not just prose: `evidence[]`, `excluded_evidence_ids`,
  `concerns[{id, text, evidence_ids}]`, `charts[{id, spec}]`, `report_sections`.
- Feedback (targeted at a concern / evidence / chart / section, or general) goes through
  a small **feedback-router node** that turns it into operations:
  `exclude_evidence`, `update_chart`, `investigate(e.g. previous names)`, `revise_section`, `note`.
- Dependency tracking: only concerns citing changed evidence, affected charts and the
  report sections that depend on them are recomputed. Untouched work is reused.
- Idempotency: every command carries a client `command_id` (unique in `commands` table +
  dedupe in workflow). Same feedback sent twice → second is a no-op (evidence already excluded).
- Long sessions (10+ rounds): workflow uses **continue-as-new** after N rounds to keep history small;
  LLM context is built from structured state + a short feedback log, not the whole transcript.

### Visualization (req. 08)
Tools compute data from the DB (the LLM never invents numbers) and return a chart **spec**:
1. `chart_rating_trend(company_id, from?, to?, granularity, markers[])` — monthly avg + count
2. `chart_news_timeline(company_id, exclude_ids[])`
3. `table_compare_companies(company_ids[])` and generic `table(rows)` (e.g. before/after sale)

Spec is stored in the `chart` event; frontend renders with Recharts inline. Chart feedback
("last 12 months", "mark the sale date") → `update_chart` op emits a new version of the same chart id.

### Failures (sec. 10)
- LLM calls: activity `start_to_close` timeout + retry policy with backoff; outputs parsed with
  Pydantic, one repair attempt on malformed output, then a non-retryable error → check shows
  `error` with a **Retry** button (workflow waits for a `retry` signal, nothing lost).
- Worker restart mid-stream: heartbeat timeout → Temporal retries the activity; the retry emits
  `message_reset(message_id)` so the UI drops the half-streamed text instead of duplicating it.
- API restart: stateless; SSE clients reconnect with `Last-Event-ID`.

## 3. Data model (first cut)

- `companies(id, name, status, founded, closed_on, address)` + `pg_trgm` index on name
- `company_previous_names(company_id, name)` (trigram-indexed too — Copperleaf/Leafline)
- `reviews(id, company_id, date, rating, text)` index `(company_id, date)`
- `news(id, company_id, date, headline, text)` index `(company_id, date)`
- `checks(id, input, company_id, status, created_at, updated_at, cost_usd)`
- `check_events(check_id, seq, type, payload jsonb, created_at)` PK `(check_id, seq)`
- `commands(id uuid PK, check_id, kind, payload, created_at)` — idempotency + audit
- LangGraph checkpoint tables (managed by `langgraph-checkpoint-postgres`)
- Migrations with Alembic; seed script loads **only** `companies` from `data/dummy_data.json`.

## 4. Repo layout

```
backend/   FastAPI app, Temporal workflow + activities, LangGraph agent, tools, Alembic, tests
frontend/  Next.js app (check list, check view: stream, questions, charts, report, feedback)
data/      dummy_data.json
docs/      PLAN.md, DECISIONS.md, architecture diagram
docker-compose.yml  postgres + temporal (dev) + api + worker
```

## 5. Milestones (build order)

| # | Milestone | Done when | Est. |
|---|---|---|---|
| M0 | Skeleton: compose (Postgres, Temporal), FastAPI, worker, Next.js, Alembic, seed | `docker compose up` + seed works | 0.5h |
| M1 | Data tools + LangGraph agent (search → details → reviews/news → concerns → report) | agent runs end-to-end in a script | 1h |
| M2 | Temporal workflow + event log + SSE + UI stream view | live tokens & tool steps in browser; refresh replays cleanly | 1.25h |
| M3 | Questions (single/multi/text) + idempotent answers | check_1 step 1–2, check_5 step 1–4 | 0.75h |
| M4 | Pause/resume + restart survival | check_4 step 2 & 5 | 0.75h |
| M5 | Charts (3 tools) + chart feedback | check_3 | 0.75h |
| M6 | Feedback router + selective recompute + idempotent repeats | check_2, check_5 step 5 | 1h |
| M7 | Failure handling polish, tests, README, DECISIONS.md, diagram | sample checks pass | 1h |

Stretch (only if time): report diff since last feedback, per-check AI cost, editable plan.

## 6. Testing
- pytest unit tests for tools (lookalike search, previous names, monthly aggregation).
- Temporal `WorkflowEnvironment` (time-skipping) tests with a **fake LLM**: question wait,
  pause/resume, duplicate signals, activity failure + retry.
- A scripted runner that drives the 5 sample checks through the API (sample checks stay in tests only).

## 7. Decisions
- **LLM provider/model** — Google Gemini via `langchain-google-genai` (free API key from
  Google AI Studio; supports streaming + tool calling). Default model `gemini-2.5-flash`,
  set by `LLM_MODEL` env var. The model is created in one factory (`init_chat_model`), so
  switching provider is a config change, not a code change.
  - Free-tier rate limits (low requests/minute) → 429s are treated as retryable in the
    activity retry policy with exponential backoff; keep agent steps few and batched.
  - A **fake LLM** (scripted responses) is used in tests so CI never needs a key.
- **Deployment** — default: local Docker Compose for demo; optional Neon (Postgres) + Vercel (UI).
