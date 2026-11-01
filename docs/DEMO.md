# Demo plan

Two recordings that complement each other:

- **Automated walkthrough** (`demo/`): Chrome driven by a script through the real app, with
  captions and a visible cursor, waiting time sped up. Re-recordable after any change.
- **Narrated walkthrough** (this file): you on screen or voice, same story, plus the why.
  Target 5–6 minutes. The reviewer should finish it knowing what the product does, that the
  hard requirements really work, and how you think about trade-offs.

## Before recording

```bash
docker compose up -d                                   # Postgres + Temporal
cd backend && uv run python scripts/reset_demo.py --yes  # empty check list
uv run uvicorn company_check.api.app:app --port 8000     # API
uv run python -m company_check.worker.main               # worker, in a terminal you can show
cd ../frontend && npm run dev                            # UI
```

- Browser at 1440×900 or larger, zoom 110 %, dark theme (it reads better on video), close
  other tabs and notifications.
- Have three windows ready: the app, a terminal running the worker, the Temporal UI
  (http://localhost:8233).
- Do one throwaway run first so the models are warm and you know the timings.
- Gemini streams the report in a few large chunks; to pause mid-report, click Pause as soon
  as the "Writing the report…" row appears.

## Run of show

| # | Time | Show | Say (in your words) |
|---|---|---|---|
| 1 | 0:00 | Home page | "This is Live Company Check: an agent that does due diligence on a company while a human reviewer watches and steers. Everything streams live, the agent asks when it's unsure, and feedback changes only what it affects." |
| 2 | 0:20 | Type **Brightline Logistics**, Start | "There are three companies with nearly this name. Instead of guessing, the agent asks." |
| 3 | 0:35 | Question card, click **Brightline Logistics Ltd** | "Questions are single choice, multiple choice or free text. The answer is an idempotent command; a double click does nothing extra." |
| 4 | 0:50 | Activity on the left, case file filling on the right | "Left is what the agent did, in order. Right is the current answer: company, findings with evidence ids, charts, report. Every pixel comes from one append-only event log." |
| 5 | 1:10 | Click **Pause** as the report starts. Wait. **Resume** | "Pause takes effect mid-sentence. On resume the model continues the same text instead of starting over, so the reviewer never sees a repeat." |
| 6 | 1:40 | Terminal: `kill -9` the worker while a report streams; start it again | "Now the worst case: the worker dies mid-report. Temporal retries the activity, LangGraph resumes from its Postgres checkpoint, and the text continues where it stopped. The UI shows 'Recovering', then carries on." |
| 7 | 2:10 | Refresh the browser | "A refresh replays the event log and shows exactly the same thing. That's tested: the visible text always equals the final report." |
| 8 | 2:25 | New check: **Pinecrest Home Services** | "Here the ratings drop after the company was sold. The agent spots it and charts it without being asked." |
| 9 | 2:45 | Chart → **Feedback**: "Only show the last 12 months" | "Feedback can target a chart, a concern, an evidence item or the report. The chart is updated in place: same id, version 2. Nothing else is recomputed." |
| 10 | 3:05 | "Mark the date the company was sold" | "Updates merge: the 12-month range is kept and the marker added." |
| 11 | 3:20 | New check: **Northwind Dental Supplies**. Click evidence chip **ev_news_004_02** → "These two articles are about a dental clinic, leave them out" | "Evidence-level feedback. The exclusion is applied deterministically: only concerns and charts that cited it are recomputed." |
| 12 | 3:45 | Report → **Highlight changes** | "The report shows what changed since the last version." |
| 13 | 3:55 | Send the same feedback twice | "Repeats are caught before the LLM is called: 'already applied', and the token counter in the header doesn't move." |
| 14 | 4:10 | Temporal UI: the workflow, its signals and history | "Each check is one Temporal workflow. It stays open for feedback, survives restarts and long pauses, and continues as new every 8 rounds so history stays small." |
| 15 | 4:30 | `docs/ARCHITECTURE.md` diagram | "FastAPI takes commands and serves the stream over SSE. Temporal owns control flow, LangGraph owns the agent inside one activity, Postgres holds the data, the event log and the checkpoints." |
| 16 | 5:00 | Terminal: `uv run python scripts/run_samples.py` output | "All five sample checks run end to end through the API with assertions, 26 out of 26. Plus 42 backend and 6 frontend tests that never call the LLM." |
| 17 | 5:15 | Repo README | "Trade-offs I'd revisit with more time: a shared rate limiter for several workers, auth, and evaluation of the report text." Close. |

## Points worth saying out loud

- **Why Temporal and LangGraph together:** Temporal makes the long-lived parts durable
  (pauses for hours, restarts, retries, queued feedback). LangGraph makes the agent steps
  resumable. Neither alone covers both.
- **Why an event log:** live view, reconnect, refresh and replay are the same code path. The
  UI is a pure reducer over events, which is why it's testable.
- **Selective recompute:** feedback runs a small round with patch tools (exclude evidence,
  update findings, revise report, update chart) on a compact context, not a full re-run.
- **Free-tier constraints shaped choices:** Flash-Lite with a Flash fallback, a client-side
  request limiter, token usage shown per check.
- **What the agent can't do:** it only sees the seeded data; sample checks are test-only and
  never reach the agent.

## If something goes wrong on camera

- A 429 or 503: the check shows a clear message and Retry resumes from the checkpoint. That
  is itself worth showing.
- The agent words things differently each run; the structure (questions, ids, charts) is
  stable. Don't script exact sentences.
