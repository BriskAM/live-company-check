# Decisions and Trade-offs

Living document: each decision says what was chosen, why, and what it costs.

## Architecture

### One owner per concern
| Concern | Owner |
|---|---|
| Control flow (running / waiting / paused / error) | Temporal workflow, one per check |
| Agent reasoning | LangGraph graph, run inside a Temporal activity |
| Agent progress between steps | LangGraph Postgres checkpointer (thread = check id) |
| What the reviewer sees | Append-only `check_events` table |
| Domain data | Postgres tables, read only through tool functions |

**Why:** each failure mode has exactly one place to look. Temporal survives restarts and can
wait for hours for free; LangGraph checkpoints make "resume where it stopped" a property of
the graph rather than custom code; the event log makes the UI replayable.

**Trade-off:** two sources of state (Temporal history + LangGraph checkpoint). They are kept
consistent by making the activity derive what to do from the checkpoint (see below), so the
workflow never needs to know the graph's internals.

### LangGraph runs inside an activity, not in the workflow
Workflow code must be deterministic; LLM calls and DB reads are not. The workflow only
decides *when* to run the agent; the activity runs it.

### The activity decides how to enter the graph
`run_agent` reads the graph's last checkpoint and picks one of: start fresh, resume pending
interrupts with the answers it was given, or continue from the last completed node. The
workflow always passes *all* answers accepted so far.

**Why:** retries, pauses and restarts all become the same operation ("run again"), and
re-sending answers is harmless.

### Deterministic steps where possible
Company resolution and data gathering are plain code, not LLM calls. The LLM is used for
analysis (a short tool loop) and writing the report.

**Why:** cheaper on a rate-limited free tier, faster, and reliable on the lookalike-name
trap. Statistics (monthly averages, rating change point) are computed in Python and handed
to the LLM, which never does arithmetic.

**Trade-off:** less "agentic" — the agent cannot decide to skip gathering. Acceptable for a
due-diligence check where gathering is always needed.

### LLM call and tool execution are separate graph nodes
`interrupt()` re-runs the node it is in when resumed. Putting questions in the `tools` node
(after the LLM's message is checkpointed) means answering a question never repeats the LLM
call, so the question cannot change between the reviewer seeing it and answering it.

## Streaming and the event log

### UI state is a fold over an append-only log
Every visible thing is an event with a per-check `seq`. The browser's reducer is the only
code that turns events into UI, for both live updates and a full replay after refresh.

**Why:** refresh mid-stream shows exactly what was there, with no gaps or repeats, by
construction. `EventSource` reconnects with `Last-Event-ID` automatically.

### Per-check sequence under a row lock (not a global serial)
`checks.last_seq` is incremented with `SELECT … FOR UPDATE` in the same transaction as the
insert. **Why:** a global `bigserial` can commit out of order, and a reader could skip an
event. **Cost:** writers for one check serialise — fine, there is effectively one writer.

### Idempotency keys on events
Events that a retried step may emit again (tool steps, questions, findings, reports) carry a
deterministic key (from the LangGraph step number or tool-call id). Duplicates are dropped
in the insert transaction.

### Streamed text is batched
Token deltas are buffered and written at most every 100 ms per message, and flushed before
any other event to keep order. **Trade-off:** up to 100 ms of extra latency and, on a hard
crash, up to 100 ms of text lost — but that text was never shown either, so the UI stays
consistent with the log.

### `message_start` resets half-streamed text
The first delta of a message in a new attempt emits `message_start`; the UI clears any text
it had for that id. A retried step therefore replaces, not duplicates, partial output.

### Interrupted reports are continued, not rewritten
After a pause or a crash the report step reads back what was already streamed for its
message id and asks the model to continue from exactly there. The first ~160 characters of
the continuation are held back and checked: a repeated tail is trimmed; a restart from the
top resets the message (`message_start`) so the reviewer never sees text twice. A message
that already finished is reused without an LLM call.

**Trade-off:** a seam can read slightly less smoothly than one uninterrupted generation,
and only the report gets this treatment (analyst notes are short and restarted).

### SSE with LISTEN/NOTIFY, one connection per stream
LISTEN starts before the replay query so nothing committed in between is missed. A 5 s
timeout re-polls as a safety net. **Trade-off:** one Postgres connection per open check
page — fine for a reviewer tool; a shared listener would be needed at scale.

## Reviewer commands

### Every command carries a client-generated `command_id`
Stored in `commands` (primary key) and deduped again in the workflow. The UI creates the id
once per user action and reuses it on retry.

**Why:** double clicks, retried requests and a signal that fails after the DB write are all
safe. The API signals on every request (even repeats) so a lost first signal is recovered.

### First answer per question wins
The workflow ignores answers to questions that are not pending or already answered.

### Answers are accepted while paused
The answer is stored and applied on resume. Blocking it would be worse UX, and answering while
a pause is in progress is a case the system should handle.

### Pause = cancel the activity
Pause cancels the running activity; it flushes streamed text and stops. Resume re-runs it
from the checkpoint. A flag records that the cancellation came from a pause, so a resume
arriving before the activity has stopped is not mistaken for an error.

## LLM

### Gemini 3.5 Flash-Lite, falling back to 3.5 Flash
Flash-Lite is primary because it stretches the free-tier quota further and is faster; Flash
is the fallback (separate quota). Candidates were probed on 2026-11-01: 2.5 models are unavailable to new keys, pro models
have zero free-tier quota, and 3.6–3.8 Flash often returned 503. Fallback triggers on any
error before the first streamed chunk, so one message never mixes models.

All models are created in `agent/llm.py`; switching provider is a change to that file.

### Permanent LLM errors fail fast
401/402/403 and invalid-key errors are raised as non-retryable, so the check goes straight
to `error` with the message and waits for the reviewer's Retry. Overloads (503), rate limits
(429) and timeouts keep the normal retry policy with backoff.

### Validation instead of trust
`submit_findings` rejects unknown or excluded evidence ids, and malformed tool calls are
returned to the model with the error so it can fix them. A turn limit stops loops.

## Visualization

### Charts are specs computed from data; the LLM only picks parameters
Four tools: `chart_rating_trend`, `chart_news_timeline`, `table_rating_periods`,
`table_compare_companies`. Handlers compute the data from state (respecting excluded
evidence) and emit a JSON spec the UI renders with Recharts / HTML. The LLM gets a short
text summary back, never raw data to restate. "Last N months" is a parameter resolved in
code against the check's as-of date, so the model does no date arithmetic.

### Update by id, not a new chart
Every chart tool takes an optional `chart_id`; with it, the call updates that chart and bumps
its version, and omitted fields keep their previous values (a new range replaces the old
range). The UI shows the new version where the update happened and collapses older versions
to an "updated below" line, so the reviewer sees the change without scrolling up.

### Visual rules
One series per chart, so no legend (the title names it); no dual axis (review counts are in
the tooltip and the data table, not on a second y-axis); 2px line, ≥8px dots with a surface
ring; hairline solid grid; event markers as neutral annotation lines; every chart has a
"Show data" table view.

## Feedback rounds

### The workflow stays open and queues feedback
After the check completes, the workflow waits for feedback. Feedback can be sent at any
time; while the agent is busy (or paused) it is queued and handled in order. Each round is
one run of the agent with the feedback as input; questions, pause and retry work inside a
round exactly as in the first run. A completed check with no feedback for 30 days closes.

### A round patches state; it doesn't redo the check
A feedback round gets a fresh, compact context (dossier + current findings, charts, earlier
answers and a short feedback log), not the whole transcript, and a different tool set:
`exclude_evidence`, `update_findings` (patch named concerns only), chart tools with
`chart_id`, `revise_report`, `search_news`, `switch_company`. It ends when the analyst replies
without calling a tool.

What gets recomputed is decided in code, not by the model:
- Excluding evidence removes concerns that relied only on it, takes it off others, and
  redraws only charts whose data changed (compared by a digest of the chart's data).
- The report is rewritten only when something it reads changed (a concern, the
  recommendation, an excluded review) or the reviewer asked for wording changes — and it is
  revised from the version the reviewer read, keeping untouched parts.
- Wording requests are kept and passed to every later rewrite, so round 7 doesn't undo
  round 3.
- "I meant the other company" restarts the check for that company (state reset; chart ids
  keep counting up so old charts on screen stay distinct).

**Trade-off:** the model still decides *which* operations a piece of feedback needs; a
misread is visible in the round's "Applied: …" summary and can be corrected with more
feedback.

### Repeated feedback is a no-op without an LLM call
Two layers: the same `command_id` is dropped by the API and workflow (double click, retry).
A *new* message with the same text (normalised) is skipped deterministically when it cannot
change anything: it matches the last applied feedback, or an earlier one that only excluded
evidence that is still excluded (exclusions are never undone). Other repeats ("make it
shorter" again) go to the model on purpose.

### Continue-as-new every 8 rounds
Keeps Temporal history small for long review sessions. The new run carries the queue and the
last 500 command ids (so late repeats are still recognised); the agent's own state is in the
LangGraph checkpoint and isn't affected.

## Testing
- Unit tests for search, stats and company picking (seeded DB).
- Workflow tests on Temporal's time-skipping server with fake activities: answers and
  double clicks, pause/resume, answers while paused, failure then retry, feedback queued
  while running, questions inside a feedback round, continue-as-new.
- Feedback tools without the LLM: exclusion touches only dependent concerns and charts,
  repeats are no-ops, patches leave other concerns alone, a feedback round starts once.
- The agent is exercised against the real LLM through a CLI runner; tests never call the LLM.
